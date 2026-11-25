"""Local, evidence-bound historical quality review; never issues training approval."""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path

from atlaspm.data import factor_db, unified, unified_pipeline
from atlaspm.data import hf_stock5m_security_master_v53 as ledger
from atlaspm.data import hf_stock5m_reconcile as reconcile
from atlaspm.data import hf_stock5m_session_v4 as calendar
from atlaspm.data.hf_stock5m_certification_v5 import fingerprint


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _ledger_digest():
    # Hash logical rows rather than a potentially stale SQLite main file/WAL.
    path=unified.private_path(ledger.BASE/"ledger.sqlite3")
    if not path.is_file():
        raise unified.DataUnavailable("Reviewed identity/action ledger missing")
    h=sha256()
    with closing(ledger._db(read_only=True)) as con:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        ledger._verify_ledger(con)
        cursor=con.execute("SELECT claim_id,claim_json FROM claims ORDER BY claim_id")
        while rows:=cursor.fetchmany(2048):
            for row in rows:
                h.update(json.dumps(row,separators=(",",":")).encode()+b"\n")
    return h.hexdigest()


def _finish(report):
    report["blockers"]=sorted(set(report["blockers"]))
    report["evidence_id"]=_digest(report)
    return report


def review_month(month, *, reference_csv=None, reference_symbols=(),
                 memory="256MB", threads=1):
    """Check all observed factor symbol-days against prior-known ledger members.

    A reviewed claim is not independent proof. Scope is one month of HF factors;
    expected-member gaps refer only to the known claim ledger, not all listings.
    Optional independent price reconciliation is a bounded reference sample.
    No inferred IPO, delisting or total return, and no remote requests.
    """
    lineage=unified_pipeline.factor_preflight(month,month,memory=memory,threads=threads)
    blockers=list(lineage["blockers"])
    report={"month":month,"factor_lineage_id":lineage["evidence_id"],
            "structural_preflight_passed":lineage["structural_preflight_passed"],
            "status":"BLOCK_REAL_TRAINING","approved_for_model_training":False,
            "approved_for_backtest":False,"point_in_time_verified":False,
            "live_execution_enabled":False,"blockers":blockers,"days":[]}
    if not lineage["structural_preflight_passed"]:
        blockers.append("FACTOR_LINEAGE_BLOCKED")
        return _finish(report)
    try:
        before=_ledger_digest()
    except (OSError,ValueError):
        blockers.append("IDENTITY_ACTION_LEDGER_UNAVAILABLE_OR_CORRUPT")
        return _finish(report)
    path=factor_db._path(month,"hf","rth","factors")
    factor_before=fingerprint(path)["sha256"]
    if factor_before!=lineage["months"][0]["content"]["factors"]["sha256"]:
        raise unified.DataQualityError("Factor changed after lineage preflight")
    totals=Counter()
    with closing(factor_db._db(memory,threads)) as con:
        src=factor_db._files_sql([path])
        sessions=calendar.calendar_rows(month)
        if not sessions:
            blockers.append("NO_EXCHANGE_SESSIONS")
        expected_days={d.isoformat() for d,*_ in sessions}
        seen_days={str(row[0]) for row in con.execute(f"SELECT DISTINCT trade_date FROM {src}").fetchall()}
        report["unexpected_factor_days"]=sorted(seen_days-expected_days)
        if report["unexpected_factor_days"]:
            blockers.append("UNEXPECTED_FACTOR_DATES")
        for day,*_ in sessions:
            day=day.isoformat()
            snapshot=unified.review_security(as_of=day,trade_date=day)
            members={x["symbol"]:x["security_id"] for x in snapshot["members"]}
            remaining=set(members)
            observed=mapped=unmapped=0
            examples=[]
            cur=con.execute(f"SELECT symbol FROM {src} WHERE trade_date=? ORDER BY symbol",[day])
            while batch:=cur.fetchmany(2048):
                for (symbol,) in batch:
                    observed+=1
                    if symbol in members:
                        mapped+=1
                        remaining.discard(symbol)
                    else:
                        unmapped+=1
                        if len(examples)<10:
                            examples.append(symbol)
            record={"date":day,"observed":observed,"mapped_to_prior_known_claim":mapped,
                    "unmapped":unmapped,"known_members_without_factor":len(remaining),
                    "unresolved_claims":len(snapshot["unresolved"]),
                    "visible_actions":dict(Counter(x["kind"] for x in snapshot["actions"]
                                                  if x["effective_date"]<=day)),
                    "snapshot_sha256":_digest(snapshot),"unmapped_examples":examples}
            report["days"].append(record)
            totals.update({k:record[k] for k in ("observed","mapped_to_prior_known_claim",
                                                "unmapped","known_members_without_factor","unresolved_claims")})
            if not observed:
                totals["missing_sessions"]+=1
    if _ledger_digest()!=before or fingerprint(path)["sha256"]!=factor_before:
        raise unified.DataQualityError("Evidence ledger or factor revision changed during review")
    report.update(ledger_claims_sha256=before,factor_sha256=factor_before,totals=dict(totals))
    for k,reason in (("unmapped","OBSERVED_IDENTITIES_UNMAPPED"),("known_members_without_factor","KNOWN_MEMBERS_MISSING_FACTORS"),
                     ("unresolved_claims","UNRESOLVED_IDENTITY_OR_ACTION_CLAIMS"),
                     ("missing_sessions","MISSING_FACTOR_SESSIONS")):
        if totals[k]:
            blockers.append(reason)
    if not totals["observed"]:
        blockers.append("NO_FACTOR_OBSERVATIONS")
    if reference_csv is not None:
        if not reference_symbols or isinstance(reference_symbols,str):
            raise ValueError("Explicit reference symbols required")
        p=unified.private_path(Path(reference_csv),exists=True)
        ref_before=fingerprint(p)["sha256"]
        bar_path=unified.hf_stock5m.output_path(month)
        bar_before=fingerprint(bar_path)["sha256"]
        result=reconcile.reconcile(month,symbols=list(reference_symbols),reference_csv=p,save=False)
        if fingerprint(p)["sha256"]!=ref_before or fingerprint(bar_path)["sha256"]!=bar_before:
            raise unified.DataQualityError("Reference or bars changed during reconciliation")
        if bar_before!=lineage["months"][0]["content"]["bars"]["sha256"]:
            raise unified.DataQualityError("Bars changed after factor lineage preflight")
        report["reference_sample"]={"sha256":ref_before,"result":result}
        if not result["reference_prices_reconciled"]:
            blockers.append("REFERENCE_SAMPLE_RECONCILIATION_FAILED")
    else:
        blockers.append("REFERENCE_PRICE_SAMPLE_NOT_PROVIDED")
    return _finish(report)


def main():
    p=ArgumentParser(description=__doc__)
    p.add_argument("--month",required=True)
    p.add_argument("--reference-csv")
    p.add_argument("--reference-symbol",action="append",default=[])
    p.add_argument("--memory",default="256MB")
    p.add_argument("--threads",type=int,default=1)
    a=p.parse_args()
    print(json.dumps(review_month(a.month,reference_csv=a.reference_csv,
          reference_symbols=a.reference_symbol,memory=a.memory,threads=a.threads),indent=2))


if __name__=="__main__":
    main()
