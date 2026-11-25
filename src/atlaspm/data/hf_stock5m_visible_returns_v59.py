"""Bounded retrospective return reconciliation using reviewed V5.3 evidence.

No model training or certified total returns. Input marks must explicitly be
declared unadjusted; this declaration is not independent vendor verification.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
from decimal import Decimal, localcontext
import json
from pathlib import Path

from atlaspm.data import hf_stock5m_security_master_v53 as ledger
from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data import hf_stock5m_session_v4 as v4
from atlaspm.data.hf_stock5m_certification_v5 import ROOT, fingerprint
from atlaspm.data.market_db import private_path

BASE=ROOT/"returns_v59"
FIELDS={"security_id","start_date","end_date","start_symbol","end_symbol","start_close","end_close"}


def _interval(row,identities,actions,as_of):
    if not isinstance(row,dict) or set(row)!=FIELDS:
        raise ValueError("INVALID_RETURN_FIELDS")
    ledger._token(row["security_id"])
    start,end=ledger._date(row["start_date"]),ledger._date(row["end_date"])
    if not start<end<as_of:
        raise ValueError("RETURN_END_MUST_PRECEDE_AS_OF")
    for field in ("start_symbol","end_symbol"):
        if not isinstance(row[field],str) or not ledger.SYMBOL.fullmatch(row[field]):
            raise ValueError("INVALID_RETURN_SYMBOL")
    p0,p1=(Decimal(ledger._decimal(row[k])) for k in ("start_close","end_close"))
    intervals=sorted((i for i in identities if i["valid_from"]<=end and
                      (i["valid_to"] is None or i["valid_to"]>start)),key=lambda i:i["valid_from"])
    if not intervals or intervals[0]["valid_from"]>start:
        raise ValueError("MISSING_START_IDENTITY")
    if intervals[0]["symbol"]!=row["start_symbol"] or intervals[-1]["symbol"]!=row["end_symbol"]:
        raise ValueError("ENDPOINT_SYMBOL_MISMATCH")
    if intervals[-1]["valid_to"] is not None and intervals[-1]["valid_to"]<=end:
        raise ValueError("MISSING_END_IDENTITY")
    relevant=[a for a in actions if start<a["effective_date"]<=end]
    if any(a["kind"] in ("CASH_EXIT","DELISTING","MERGER") for a in relevant):
        raise ValueError("UNSUPPORTED_EXIT_ECONOMICS")
    transitions=set()
    for left,right in zip(intervals,intervals[1:]):
        boundary=right["valid_from"]
        if left["valid_to"]!=boundary:
            raise ValueError("IDENTITY_INTERVAL_GAP")
        if left["exchange"]!=right["exchange"]:
            raise ValueError("EXCHANGE_TRANSITION_REVIEW_REQUIRED")
        if left["symbol"]!=right["symbol"]:
            transitions.add((boundary,right["symbol"]))
    recorded={(a["effective_date"],a["new_symbol"]) for a in relevant if a["kind"]=="TICKER_CHANGE"}
    if transitions!=recorded:
        raise ValueError("TICKER_TRANSITION_NOT_RECONCILED")
    economic=defaultdict(list)
    for a in relevant:
        if a["kind"] in ("SPLIT","CASH_DIVIDEND"):
            economic[a["effective_date"]].append(a)
    with localcontext() as ctx:
        ctx.prec=50
        shares,cash=Decimal(1),Decimal(0)
        for day,events in sorted(economic.items()):
            if len(events)!=1:
                # Same-day split and dividend: pre/post-split share basis
                # is not stored in V5.3; never invent a sequencing rule.
                raise ValueError("AMBIGUOUS_SAME_DAY_ACTION_BASIS:"+day)
            a=events[0]
            if a["kind"]=="SPLIT":
                shares*=Decimal(a["value"])
            else:
                cash+=shares*Decimal(a["value"])
        price_return=shares*p1/p0-1
        economic_return=(shares*p1+cash)/p0-1
        return {**row,"status":"VISIBLE_ACTION_RETURN_REVIEW_REQUIRED",
                "split_adjusted_start_close":str(p0/shares),
                "ending_shares_per_start_share":str(shares),
                "cash_dividend_entitlement_per_start_share":str(cash),
                "split_adjusted_price_return":str(price_return),
                "visible_action_return_no_reinvestment":str(economic_return),
                "identity_record_ids":[i["record_id"] for i in intervals],
                "action_record_ids":[a["record_id"] for a in relevant],
                "latest_visible_evidence_date":max([i["known_date"] for i in intervals]+
                                                  [a["known_date"] for a in relevant]),
                "start_identity_known_before_start":intervals[0]["known_date"]<start,
                "completeness_verified":False,"approved_for_model_training":False}


def run(filename,as_of,*,raw_unadjusted=False):
    as_of=ledger._date(as_of)
    if raw_unadjusted is not True:
        raise ValueError("EXPLICIT_UNADJUSTED_MARK_DECLARATION_REQUIRED")
    path=private_path(Path(filename),exists=True)
    before=fingerprint(path)
    if before["bytes"]>4*1024*1024:
        raise ValueError("MARK_INPUT_SIZE_LIMIT")
    rows=[]
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                if len(rows)>=1000:
                    raise ValueError("MARK_INPUT_ROW_LIMIT")
                rows.append(json.loads(line))
    if not rows:
        raise ValueError("EMPTY_MARK_INPUT")
    if fingerprint(path)!=before:
        raise ValueError("MARK_INPUT_CHANGED")
    snap=ledger.snapshot(as_of)
    ids,acts=defaultdict(list),defaultdict(list)
    for i in snap["identities"]:
        ids[i["security_id"]].append(i)
    for a in snap["actions"]:
        acts[a["security_id"]].append(a)
    results=[]
    for number,row in enumerate(rows,1):
        try:
            if snap["unresolved"]:
                raise ValueError("UNRESOLVED_VISIBLE_LEDGER_CLAIMS")
            if not isinstance(row,dict) or set(row)!=FIELDS:
                raise ValueError("INVALID_RETURN_FIELDS")
            sid=ledger._token(row["security_id"])
            result=_interval(row,ids[sid],acts[sid],as_of)
            results.append({"row":number,**result})
        except (ValueError,TypeError,KeyError) as exc:
            results.append({"row":number,"status":"BLOCKED","reason":str(exc)})
    report={"schema":"atlaspm_visible_action_returns_v59",**v4.GUARDS,
            "as_of":as_of,"marks_input":before,
            "rows_requested":len(rows),"rows_computed":sum(r["status"]!="BLOCKED" for r in results),
            "status_counts":dict(Counter(r["status"] for r in results)),
            "unresolved_visible_claims":len(snap["unresolved"]),"results":results,
            "status":"RESEARCH_RETURNS_REVIEW_REQUIRED",
            "completeness_verified":False,"raw_price_semantics_verified":False,
            "warning":"Retrospective entitlement calculation, no dividend reinvestment or payment timing; not a certified total-return label. Later-known revisions are excluded."}
    ev._json(BASE/"latest.json",report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file",type=Path,required=True)
    p.add_argument("--as-of",required=True)
    p.add_argument("--raw-unadjusted",action="store_true")
    a=p.parse_args()
    print(json.dumps(run(a.file,a.as_of,raw_unadjusted=a.raw_unadjusted),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
