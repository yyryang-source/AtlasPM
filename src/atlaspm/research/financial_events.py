"""Bounded local financial event reduction; no order or automatic AI egress.

Events are normalized internal records, NOT claimed vendor wire schemas.
Unknown original availability and future effective dates remain explicit.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re

from atlaspm.data.market_db import private_path
from atlaspm.data.provider_policy import ROLES, local_rights, RIGHTS_ENV, require_enabled

KINDS = {"news":2, "analyst_rating":3, "earnings_forecast":3, "credit_rating":4,
         "credit_outlook":3, "credit_watch":4, "credit_withdrawal":4, "default":5,
         "earnings":4, "sec_disclosure":4, "options_flow":3, "volume_anomaly":3,
         "macro_release":4, "rate_decision":5, "index_change":3,
         "corporate_action":4, "position_risk":5, "bond_trade":2}
_ALLOWED = {
    "yahoo_finance":{"news","earnings"}, "bloomberg":{"news","macro_release","rate_decision","earnings"},
    "investment_banks":{"analyst_rating","earnings_forecast"},
    "sec":{"sec_disclosure","earnings","corporate_action"},
    "federal_reserve":{"macro_release","rate_decision"},
    "fred":{"macro_release"}, "treasury":{"macro_release"},
    "unusual_whales":{"options_flow"}, "ibkr":{"volume_anomaly","position_risk"},
    "finra":{"bond_trade"}, "msrb":{"bond_trade","sec_disclosure"},
}
for _agency in ("xpressapi","sp_ratings","moodys","fitch","dbrs","kbra"):
    _ALLOWED[_agency]={"credit_rating","credit_outlook","credit_watch","credit_withdrawal","default"}
for _index in ("sp_dji","msci","ftse_russell"):
    _ALLOWED[_index]={"index_change"}
FLAGS = {"research_only":True,"approved_for_model_training":False,"approved_for_backtest":False,
         "broker_order_submission_enabled":False,"live_execution_enabled":False,
         "external_ai_allowed":False,"point_in_time_verified":False}


def _time(value):
    if not isinstance(value,str) or len(value)>64:
        raise ValueError("Expected offset-aware timestamp")
    t=datetime.fromisoformat(value)
    if t.utcoffset() is None:
        raise ValueError("Timestamp requires offset")
    return t.astimezone(UTC)


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,
                             allow_nan=False).encode()).hexdigest()


def _token(value):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9._:/+-]{1,160}",value):
        raise ValueError("Invalid event identifier")
    return value


def normalize(row, *, received_at):
    fields={"provider","source_id","kind","subjects","published_at","first_available_at",
            "effective_at","evidence_sha256","delivery","expiry_seconds"}
    if not isinstance(row,dict) or set(row)!=fields:
        raise ValueError("Unexpected event schema")
    provider,kind=row["provider"],row["kind"]
    if provider not in ROLES or kind not in _ALLOWED.get(provider,set()):
        raise ValueError("Provider/event category mismatch")
    require_enabled(provider)
    if provider in RIGHTS_ENV:
        local_rights(provider)
    received=_time(received_at)
    if received>datetime.now(UTC):
        raise ValueError("Future reception")
    pub,available,effective=(_time(row[k]) if row[k] is not None else None
                             for k in ("published_at","first_available_at","effective_at"))
    if (pub and pub>received) or (available and available>received) or (pub and available and available<pub):
        raise ValueError("Invalid publication/availability chronology")
    subjects=row["subjects"]
    if not isinstance(subjects,list) or len(subjects)>64:
        raise ValueError("Invalid subject list")
    subjects=sorted({_token(x) for x in subjects})
    if not isinstance(row["evidence_sha256"],str) or not re.fullmatch(r"[0-9a-f]{64}",row["evidence_sha256"]):
        raise ValueError("Invalid evidence digest")
    if row["delivery"] not in {"delayed","unknown","file","poll","stream_reported"}:
        raise ValueError("Invalid delivery classification")
    ttl=row["expiry_seconds"]
    if type(ttl) is not int or not 1<=ttl<=30*86400:
        raise ValueError("Invalid event expiry")
    record={**row,"source_id":_token(row["source_id"]),"subjects":subjects,
            "published_at":pub.isoformat() if pub else None,
            "first_available_at":available.isoformat() if available else None,
            "effective_at":effective.isoformat() if effective else None}
    # Receipt excluded from revision identity; repeat observation is not new evidence.
    revision=_digest(record)
    return {**record,"event_id":_digest([provider,record["source_id"]]),"revision":revision,
            "received_at":received.isoformat(),"rights_status":"operator_attested_not_verified"
            if provider in RIGHTS_ENV else "source_review_required"}


def review(rows, *, as_of, holdings=()):
    """Bounded batch, exact revision dedup, latest locally visible revision.

    Related events share explicit subject identifiers, not inferred causation.
    Missing publication time is never classified as fresh realtime.
    """
    cutoff=_time(as_of)
    if cutoff>datetime.now(UTC) or not isinstance(rows,(list,tuple)) or len(rows)>2000:
        raise ValueError("Invalid event batch/cutoff")
    if not isinstance(holdings,(tuple,list,set)) or len(holdings)>1000:
        raise ValueError("Invalid holdings scope")
    holdings={_token(x) for x in holdings}
    revisions={}
    excluded=Counter()
    for row in rows:
        # Revalidate contract and identity; a supplied flag cannot grant authority.
        raw={k:v for k,v in row.items() if k not in
             {"received_at","event_id","revision","rights_status"}}
        item=normalize(raw,received_at=row["received_at"])
        if item["revision"]!=row.get("revision") or item["event_id"]!=row.get("event_id"):
            raise ValueError("Event revision mismatch")
        if _time(item["received_at"])>cutoff:
            excluded["after_cutoff"]+=1
            continue
        key=(item["event_id"],item["revision"])
        prior=revisions.get(key)
        if prior:
            excluded["duplicate"]+=1
            if prior["received_at"]<=item["received_at"]:
                continue
        revisions[key]=item
    visible={}
    for item in revisions.values():
        key=item["event_id"]
        prior=visible.get(key)
        if prior and prior["received_at"]==item["received_at"]:
            raise ValueError("Conflicting same-receipt revisions")
        if not prior or prior["received_at"]<item["received_at"]:
            visible[key]=item
    events=[]
    for item in visible.values():
        pub=_time(item["published_at"]) if item["published_at"] else None
        age=(cutoff-pub).total_seconds() if pub else None
        expired=(age if age is not None else (cutoff-_time(item["received_at"])).total_seconds())>item["expiry_seconds"]
        affected=sorted(holdings.intersection(item["subjects"]))
        importance=KINDS[item["kind"]]
        events.append({**item,"age_seconds":age,"expired":expired,"importance":importance,
                       "affected_holdings":affected,"effective_pending":bool(item["effective_at"] and
                       _time(item["effective_at"])>cutoff),"realtime_verified":False,
                       "analysis_completed_at":datetime.now(UTC).isoformat(),
                       "research_requested":not expired and importance>=3,
                       "risk_review_requested":not expired and (importance>=5 or bool(affected)),
                       "directional_signal":None})
    events.sort(key=lambda x:(x["importance"],x["received_at"],x["event_id"]),reverse=True)
    # Bounded association graph: shared explicit subject is not a causal link.
    index={}
    for item in events:
        for subject in item["subjects"]:
            related=index.setdefault(subject,[])
            if len(related)<9:
                related.append(item["event_id"])
    for item in events:
        item["related_event_ids"]=sorted({eid for subject in item["subjects"]
             for eid in index[subject] if eid!=item["event_id"]})[:8]
    return {**FLAGS,"model":"event_review_v1","as_of":cutoff.isoformat(),"events":events,
            "excluded":dict(excluded),"causal_propagation_verified":False,
            "research_requests":sum(e["research_requested"] for e in events),
            "risk_review_requests":sum(e["risk_review_requested"] for e in events)}


def from_news(report):
    """Reuse existing authorized news archive review, not duplicate storage."""
    return [normalize({"provider":report["provider"],"source_id":_digest(e["id"]),"kind":"news",
                       "subjects":["ticker:"+s for s in e["symbols"]],"published_at":e["published_at"],
                       "first_available_at":None,"effective_at":None,"evidence_sha256":e["evidence_id"],
                       "delivery":"file","expiry_seconds":86400},received_at=e["observed_at"])
            for e in report["events"][:1000]]


def from_options(report):
    """Convert existing alert diagnostics without deriving a trade direction."""
    stamp=report["transport"]["retrieved_at_utc"]
    return [normalize({"provider":"unusual_whales","source_id":e["alert_id"],"kind":"options_flow",
                       "subjects":["ticker:"+e["ticker"]],"published_at":e["created_at"],
                       "first_available_at":None,"effective_at":None,"evidence_sha256":_digest(e),
                       "delivery":"poll","expiry_seconds":300},received_at=stamp)
            for e in report["alerts"]]


def import_events(path):
    """Read explicitly authorized normalized metadata file; no new event database.

    Receipt is assigned now; supplied historical timestamps do not backdate it.
    """
    path=private_path(Path(path),exists=True)
    with path.open("rb") as handle:
        raw=handle.read(4*1024*1024+1)
    if len(raw)>4*1024*1024:
        raise ValueError("Event import exceeds 4 MiB")
    rows=json.loads(raw)
    if not isinstance(rows,list) or len(rows)>1000:
        raise ValueError("Event import exceeds 1000 rows")
    received=datetime.now(UTC).isoformat()
    return [normalize(row,received_at=received) for row in rows]


def from_credit(report):
    """Rating dates are not wire availability; preserve unknown timestamps."""
    stamp=report["transport"]["retrieved_at_utc"]
    result=[]
    for row in report["ratings"][:1000]:
        action=(row.get("action_raw") or "").casefold()
        kind="credit_withdrawal" if "withdraw" in action else "credit_rating"
        digest=_digest(row)
        result.append(normalize({"provider":"xpressapi","source_id":digest,"kind":kind,
            "subjects":[row["identifier_type"]+":"+row["identifier"]],"published_at":None,
            "first_available_at":None,"effective_at":None,"evidence_sha256":digest,
            "delivery":"poll","expiry_seconds":86400},received_at=stamp))
    return result


def main():
    from argparse import ArgumentParser
    parser=ArgumentParser(description=__doc__)
    parser.add_argument("--input",required=True)
    parser.add_argument("--holding",action="append",default=[])
    args=parser.parse_args()
    rows=import_events(args.input)
    report=review(rows,as_of=datetime.now(UTC).isoformat(),holdings=args.holding)
    print(json.dumps({k:v for k,v in report.items() if k!="events"},indent=2))


if __name__=="__main__":
    main()
