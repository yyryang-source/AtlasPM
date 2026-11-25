"""V5.3 append-only, date-precision historical evidence ledger.

Explicit reviewed assertions only. Source families are declared, not proven
independent. Agreement is a research candidate, never training authorization.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
import re
import sqlite3

from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data import hf_stock5m_session_v4 as v4
from atlaspm.data.hf_stock5m_certification_v5 import EXCHANGES, ROOT, fingerprint
from atlaspm.data.market_db import private_path

BASE = ROOT / "security_master_v53"
FIELDS = {"record_id","record_type","source_family","source_url","evidence_path",
          "evidence_sha256","known_date","reviewer","state","payload"}
TYPES = {"IDENTITY","ACTION"}
ACTION_KINDS = {"SPLIT","CASH_DIVIDEND","CASH_EXIT","MERGER","DELISTING","TICKER_CHANGE"}
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,31}")
SHA = re.compile(r"[0-9a-f]{64}")
MAX_CLAIM_BYTES = 16 * 1024 * 1024
MAX_CLAIM_ROWS = 10000
MAX_EVIDENCE_OBJECTS = 1000
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024 * 1024


def _date(s):
    if not isinstance(s,str) or date.fromisoformat(s).isoformat()!=s:
        raise ValueError("Dates must be canonical YYYY-MM-DD")
    return s


def _token(s):
    if not isinstance(s,str) or not TOKEN.fullmatch(s):
        raise ValueError("Invalid identifier")
    return s


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)


def _decimal(value):
    if not isinstance(value,str) or len(value)>80:
        raise ValueError("Economic values must be bounded decimal strings")
    try:
        d = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal") from exc
    if not d.is_finite() or d<=0 or d.adjusted() not in range(-18,19):
        raise ValueError("Economic value out of supported range")
    return format(d,"f").rstrip("0").rstrip(".") if "." in format(d,"f") else format(d,"f")


def _payload(kind,p):
    if not isinstance(p,dict):
        raise ValueError("Payload must be an object")
    if kind=="IDENTITY":
        if set(p)!={"security_id","symbol","exchange","valid_from","valid_to"}:
            raise ValueError("Invalid identity payload fields")
        _token(p["security_id"])
        if not isinstance(p["symbol"],str) or not SYMBOL.fullmatch(p["symbol"]) or p["exchange"] not in EXCHANGES:
            raise ValueError("Invalid ticker or MIC")
        _date(p["valid_from"])
        if p["valid_to"] is not None and _date(p["valid_to"])<=p["valid_from"]:
            raise ValueError("Invalid exclusive end")
        return dict(p)
    if set(p)!={"security_id","kind","effective_date","value","currency","successor_id","new_symbol"}:
        raise ValueError("Invalid action payload fields")
    _token(p["security_id"])
    _date(p["effective_date"])
    k=p["kind"]
    if k not in ACTION_KINDS:
        raise ValueError("Unsupported action")
    p=dict(p)
    if k in {"SPLIT","CASH_DIVIDEND","CASH_EXIT"}:
        p["value"]=_decimal(p["value"])
        if k=="SPLIT":
            if Decimal(p["value"])==1 or p["currency"] is not None:
                raise ValueError("Split must be non-unit new/old share ratio, without currency")
        elif p["currency"]!="USD":
            raise ValueError("Cash value must be USD per share")
    elif p["value"] is not None or p["currency"] is not None:
        raise ValueError("Unsupported economic terms")
    if k=="MERGER":
        if _token(p["successor_id"])==p["security_id"]:
            raise ValueError("Merger successor must differ")
    elif p["successor_id"] is not None:
        raise ValueError("Unexpected successor")
    if k=="TICKER_CHANGE":
        if not isinstance(p["new_symbol"],str) or not SYMBOL.fullmatch(p["new_symbol"]):
            raise ValueError("Invalid new ticker")
    elif p["new_symbol"] is not None:
        raise ValueError("Unexpected ticker")
    return p


def _primary_path(r):
    path=private_path(Path(r["evidence_path"]),exists=True)
    # Within our own evidence tree, only raw content-addressed bodies or
    # collector objects can be cited. Reports, queues, hints and manifests
    # are derived artifacts, not independent source bytes.
    root=private_path(ROOT)
    if path.is_relative_to(root):
        raw_roots=(private_path(ROOT/"filings_v52"/"bodies"),
                   private_path(ROOT/"collector"/"objects"))
        if path.parent not in raw_roots or path.name!=r["evidence_sha256"]+".bin":
            raise ValueError("Pipeline reports and hints are not primary source evidence")
    return path


def validate(row, checked):
    if not isinstance(row,dict) or set(row)!=FIELDS:
        raise ValueError("Unexpected reviewed claim fields")
    r=dict(row)
    for k in ("record_id","source_family","reviewer"):
        _token(r[k])
    if r["record_type"] not in TYPES or r["state"] not in {"ASSERTED","RETRACTED"}:
        raise ValueError("Unknown record type or revision state")
    _date(r["known_date"])
    if not isinstance(r["source_url"],str) or not re.fullmatch(r"https://[^\s]{1,2048}",r["source_url"]):
        raise ValueError("Require HTTPS provenance URL")
    if not isinstance(r["evidence_sha256"],str) or not SHA.fullmatch(r["evidence_sha256"]):
        raise ValueError("Invalid evidence SHA-256")
    path=_primary_path(r)
    key=str(path)
    if key not in checked:
        checked[key]=fingerprint(path)["sha256"]
    if checked[key]!=r["evidence_sha256"]:
        raise ValueError("Evidence hash mismatch")
    r["evidence_path"]=str(path.relative_to(Path.cwd().resolve()))
    if r["state"]=="RETRACTED":
        if r["payload"] is not None:
            raise ValueError("Retraction must have null payload")
    else:
        r["payload"]=_payload(r["record_type"],r["payload"])
    return r


def _db(*, read_only=False):
    path=private_path(BASE/"ledger.sqlite3")
    if read_only:
        return sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)
    path.parent.mkdir(parents=True,exist_ok=True)
    cx=sqlite3.connect(path)
    cx.execute("""CREATE TABLE IF NOT EXISTS claims(
        claim_id TEXT PRIMARY KEY, record_type TEXT NOT NULL, record_id TEXT NOT NULL,
        source_family TEXT NOT NULL, known_date TEXT NOT NULL, claim_json TEXT NOT NULL)""")
    cx.execute("""CREATE INDEX IF NOT EXISTS claims_pit
        ON claims(record_type,record_id,source_family,known_date)""")
    return cx


def _claim_batch(filename, expected_sha256):
    """Bound input and validate original evidence before opening a writer."""
    from atlaspm.data.api_gateway import _strict_json
    path=private_path(Path(filename),exists=True)
    if expected_sha256 is not None and (not isinstance(expected_sha256,str)
                                        or not SHA.fullmatch(expected_sha256)):
        raise ValueError("INVALID_EXPECTED_CLAIM_SHA256")
    with path.open("rb") as handle:
        raw=handle.read(MAX_CLAIM_BYTES+1)
    if len(raw)>MAX_CLAIM_BYTES:
        raise ValueError("CLAIM_INPUT_BYTE_BUDGET")
    digest=ev._sha(raw)
    if expected_sha256 is not None and digest!=expected_sha256:
        raise ValueError("CLAIM_INPUT_SHA256_MISMATCH")
    checked={}
    rows=[]
    evidence_bytes=0
    for line_no,line in enumerate(raw.decode("utf-8-sig").splitlines(),1):
        if not line.strip():
            continue
        if len(rows)>=MAX_CLAIM_ROWS:
            raise ValueError("CLAIM_INPUT_ROW_BUDGET")
        try:
            value=_strict_json(line)
            if set(value)!=FIELDS:
                raise ValueError("Unexpected reviewed claim fields")
            evidence=_primary_path(value)
            if evidence==path:
                raise ValueError("Claim batch cannot be primary evidence")
            if str(evidence) not in checked:
                if len(checked)>=MAX_EVIDENCE_OBJECTS:
                    raise ValueError("CLAIM_EVIDENCE_OBJECT_BUDGET")
                evidence_bytes+=evidence.stat().st_size
                if evidence_bytes>MAX_EVIDENCE_BYTES:
                    raise ValueError("CLAIM_EVIDENCE_BYTE_BUDGET")
            row=validate(value,checked)
            canonical=_canonical(row)
            key=ev._sha(canonical.encode())
            rows.append((key,row["record_type"],row["record_id"],
                         row["source_family"],row["known_date"],canonical))
        except (OSError,ValueError,TypeError,KeyError,RecursionError) as exc:
            raise ValueError(f"Claim line {line_no}: {exc}") from exc
    if not rows:
        raise ValueError("Empty claim file")
    return path,digest,rows,checked


def _recheck_batch(path,digest,checked):
    # Read the bounded input again rather than trusting file size/mtime.
    with path.open("rb") as handle:
        raw=handle.read(MAX_CLAIM_BYTES+1)
    if len(raw)>MAX_CLAIM_BYTES or ev._sha(raw)!=digest:
        raise ValueError("CLAIM_INPUT_CHANGED")
    total=0
    for evidence,expected in checked.items():
        p=private_path(Path(evidence),exists=True)
        total+=p.stat().st_size
        if total>MAX_EVIDENCE_BYTES or fingerprint(p)["sha256"]!=expected:
            raise ValueError("CLAIM_EVIDENCE_CHANGED")


def _count_batch(cx,rows,*,write):
    """Count exact duplicates against one ledger snapshot and within input."""
    seen=set()
    added=0
    for row in rows:
        key=row[0]
        if key in seen:
            continue
        seen.add(key)
        if cx is not None:
            existing=cx.execute("SELECT 1 FROM claims WHERE claim_id=?",(key,)).fetchone()
            if existing:
                continue
            if write:
                cx.execute("INSERT INTO claims VALUES (?,?,?,?,?,?)",row)
        added+=1
    return added


def ingest(filename, *, preview=False, expected_sha256=None):
    """Bounded reviewed assertions; preview never creates or edits a ledger.

    A preview checks structure, original hashes and duplicate counts, not
    agreement, authenticity or training eligibility. Commit can bind the
    exact reviewed batch bytes using expected_sha256. The duplicate counts
    are recomputed inside the write transaction, not reserved by preview.
    """
    if type(preview) is not bool:
        raise ValueError("Preview must be boolean")
    path,digest,rows,checked=_claim_batch(filename,expected_sha256)
    if preview and not private_path(BASE/"ledger.sqlite3").is_file():
        added=_count_batch(None,rows,write=False)
        _recheck_batch(path,digest,checked)
    else:
        with closing(_db(read_only=preview)) as cx,cx:
            if preview:
                cx.execute("PRAGMA query_only=ON")
            cx.execute("BEGIN" if preview else "BEGIN IMMEDIATE")
            _verify_ledger(cx)
            added=_count_batch(cx,rows,write=not preview)
            _recheck_batch(path,digest,checked)
    total=len(rows)
    return {"schema":"atlaspm_security_master_ingest_v53",**v4.GUARDS,
            "claims_read":total,"claims_added":0 if preview else added,
            "claims_reused":total-added,"claims_would_add":added if preview else None,
            "preview":preview,"input_sha256":digest,"evidence_objects":len(checked),
            "source_independence_verified":False,"coverage_verified":False,
            "status":"CLAIM_BATCH_REVIEW_REQUIRED" if preview else "REVIEWED_ASSERTIONS_IMPORTED"}


def _verify_ledger(cx):
    # Validate metadata before the date filter: changing known_date alone
    # must not make a corrupted claim disappear from a historical snapshot.
    for key,kind,record,family,known,raw in cx.execute(
            "SELECT claim_id,record_type,record_id,source_family,known_date,claim_json FROM claims"):
        try:
            row=json.loads(raw)
            if (not isinstance(row,dict) or set(row)!=FIELDS or _canonical(row)!=raw
                    or ev._sha(raw.encode())!=key
                    or (row["record_type"],row["record_id"],row["source_family"],row["known_date"])!=(kind,record,family,known)):
                raise ValueError("Claim digest or metadata mismatch")
            if kind not in TYPES or row["state"] not in ("ASSERTED","RETRACTED"):
                raise ValueError("Unknown claim type or state")
            _date(known)
            for field in ("record_id","source_family","reviewer"):
                _token(row[field])
            if row["state"]=="ASSERTED" and _payload(kind,row["payload"])!=row["payload"]:
                raise ValueError("Noncanonical claim payload")
            if row["state"]=="RETRACTED" and row["payload"] is not None:
                raise ValueError("Invalid retraction")
        except (ValueError,TypeError,KeyError) as exc:
            raise ValueError("Historical claim ledger integrity failure") from exc


def _groups(cx,as_of):
    # Same-day evidence is unavailable. Retain all latest-date ties so that
    # contradictory statements from the same source cannot be silently chosen.
    sql="""WITH visible AS (
        SELECT *,max(known_date) OVER(PARTITION BY record_type,record_id,source_family) AS last_known
        FROM claims WHERE known_date < ?)
        SELECT record_type,record_id,claim_json FROM visible
        WHERE known_date=last_known ORDER BY record_type,record_id,source_family,claim_id"""
    from itertools import groupby
    for key,group in groupby(cx.execute(sql,(as_of,)),key=lambda x:x[:2]):
        yield key,[json.loads(x[2]) for x in group]


def _resolve(rows,checked):
    values={_canonical([r["state"],r["payload"]]) for r in rows}
    if len(values)!=1:
        return None,"CONFLICTING_VISIBLE_ASSERTIONS"
    try:
        for r in rows:
            path=_primary_path(r)
            key=str(path)
            if key not in checked:
                checked[key]=fingerprint(path)["sha256"]
            if checked[key]!=r["evidence_sha256"]:
                return None,"EVIDENCE_HASH_MISMATCH"
    except (OSError,ValueError):
        return None,"EVIDENCE_UNAVAILABLE"
    if len({r["source_family"] for r in rows})<2 or len({r["evidence_sha256"] for r in rows})<2:
        return None,"INSUFFICIENT_DECLARED_CORROBORATION"
    r=rows[0]
    if r["state"]=="RETRACTED":
        return None,"RETRACTED"
    return {**r["payload"],"record_id":r["record_id"],
            "known_date":max(x["known_date"] for x in rows),
            "sources":[{k:x[k] for k in ("source_family","source_url","evidence_sha256","reviewer","known_date")} for x in rows]},None


def snapshot(as_of,trade_date=None,*,read_only=False):
    """Research-only PIT projection using latest visible revision per family."""
    as_of=_date(as_of)
    trade_date=_date(trade_date or as_of)
    if trade_date>as_of:
        raise ValueError("Cannot query a future observation")
    identities,actions,unresolved=[],[],[]
    checked={}
    retracted=0
    with closing(_db(read_only=read_only)) as cx:
        if read_only:
            cx.execute("PRAGMA query_only=ON")
        cx.execute("BEGIN")
        _verify_ledger(cx)
        for (kind,key),rows in _groups(cx,as_of):
            result,reason=_resolve(rows,checked)
            if reason=="RETRACTED":
                retracted+=1
            elif reason:
                unresolved.append({"record_type":kind,"record_id":key,"reason":reason})
            else:
                (identities if kind=="IDENTITY" else actions).append(result)
    # Never choose between overlapping assignments, even if both have agreement.
    bad=set()
    for field in ("symbol","security_id"):
        groups=defaultdict(list)
        for r in identities:
            groups[r[field]].append(r)
        for group in groups.values():
            group.sort(key=lambda x:x["valid_from"])
            active=[]
            for row in group:
                active=[x for x in active if x["valid_to"] is None or x["valid_to"]>row["valid_from"]]
                if active:
                    bad.add(row["record_id"])
                    bad.update(x["record_id"] for x in active)
                active.append(row)
    unresolved.extend({"record_type":"IDENTITY","record_id":key,"reason":"OVERLAPPING_IDENTITY"}
                      for key in sorted(bad))
    identities=[x for x in identities if x["record_id"] not in bad]
    by_id=defaultdict(list)
    for r in identities:
        by_id[r["security_id"]].append(r)
    economic=defaultdict(list)
    for a in actions:
        economic[(a["security_id"],a["kind"],a["effective_date"])].append(a)
    valid_actions=[]
    for group in economic.values():
        for a in group:
            if len(group)>1:
                reason="DUPLICATE_OR_CONFLICTING_ECONOMIC_EVENT"
            elif not any(i["valid_from"]<=a["effective_date"] and
                         (i["valid_to"] is None or a["effective_date"]<i["valid_to"] or
                          (a["kind"] in {"CASH_EXIT","DELISTING","MERGER","TICKER_CHANGE"} and a["effective_date"]==i["valid_to"]))
                         for i in by_id[a["security_id"]]):
                reason="NO_CORROBORATED_EFFECTIVE_IDENTITY"
            else:
                valid_actions.append(a)
                continue
            unresolved.append({"record_type":"ACTION","record_id":a["record_id"],"reason":reason})
    members=[i for i in identities if i["valid_from"]<=trade_date and
             (i["valid_to"] is None or trade_date<i["valid_to"])]
    return {"schema":"atlaspm_security_master_snapshot_v53",**v4.GUARDS,
            "as_of":as_of,"trade_date":trade_date,"identities":identities,"members":members,
            "actions":valid_actions,"unresolved":unresolved,"retracted_records":retracted,
            "status":"EVIDENCE_REVIEW_REQUIRED",
            "blockers":["SOURCE_INDEPENDENCE_AND_AUTHENTICITY_UNVERIFIED",
                        "HISTORICAL_UNIVERSE_AND_ACTION_COMPLETENESS_UNVERIFIED",
                        "PRICE_ADJUSTMENT_SEMANTICS_AND_RIGHTS_UNVERIFIED"]}


def adjustment(security_id,bar_date,as_of,*,raw_unadjusted=False):
    _token(security_id)
    bar_date=_date(bar_date)
    report=snapshot(as_of,bar_date)
    if not raw_unadjusted or report["unresolved"]:
        raise ValueError("Require explicit unadjusted input and no unresolved visible evidence")
    if not any(i["security_id"]==security_id for i in report["members"]):
        raise ValueError("No corroborated security identity on bar date")
    # This is only the multiplier from currently visible assertions. Unknown
    # actions cannot be excluded and completeness is never claimed.
    ratio=Decimal(1)
    with localcontext() as ctx:
        ctx.prec=50
        for a in report["actions"]:
            if a["security_id"]==security_id and bar_date<a["effective_date"]<=as_of:
                if a["kind"]=="SPLIT":
                    ratio*=Decimal(a["value"])
                elif a["kind"] in {"CASH_EXIT","MERGER","DELISTING"}:
                    raise ValueError("Unsupported exit economics in adjustment interval")
        price=Decimal(1)/ratio
    return {"schema":"atlaspm_visible_split_adjustment_v53",**v4.GUARDS,
            "security_id":security_id,"bar_date":bar_date,"as_of":as_of,
            "price_multiplier":str(price),"volume_multiplier":str(ratio),
            "status":"PROVISIONAL_VISIBLE_ACTIONS_ONLY","completeness_verified":False}


def inventory():
    with closing(_db()) as cx:
        counts=dict(cx.execute("SELECT record_type,count(*) FROM claims GROUP BY record_type").fetchall())
    hints=private_path(ROOT/"filings_v52"/"hints")
    report={"schema":"atlaspm_security_master_inventory_v53",**v4.GUARDS,
            "claim_counts":counts,"unverified_filing_hint_files":sum(1 for _ in hints.glob("*.json")),
            "status":"EVIDENCE_REVIEW_REQUIRED",
            "blockers":["REVIEWED_CLAIMS_REQUIRED"] if not counts else ["EXTERNAL_CERTIFICATION_REQUIRED"]}
    report["summary_path"]=str(private_path(BASE/"latest.json"))
    ev._json(BASE/"latest.json",report)
    return report
