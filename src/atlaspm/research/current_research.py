"""Current descriptive research coverage from the existing event queue.

Read-only observation of retained records, not a historical as-of database.
Unknown legacy receipt/completion times do not confer current coverage.
No new forecast, independent identity, provider availability or order authority.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import UTC, datetime
import re

from atlaspm.data.provider_policy import RIGHTS_ENV, local_rights, require_enabled
from atlaspm.research import event_queue as queue
from atlaspm.research.financial_events import FLAGS, _time, normalize
from atlaspm.research.research_contract import ASSETS, CATEGORIES, validate_identity
from atlaspm.research.training_registry import digest

SHA=re.compile(r"[0-9a-f]{64}")
ROUTE_PROVIDER={"yahoo_finance":"yahoo_finance","bloomberg":"bloomberg",
                "credit_history":"xpressapi","options_local":"unusual_whales",
                "macro_local":"fred","treasury_local":"treasury","sec_local":"sec"}


def _sha(value):
    return isinstance(value,str) and SHA.fullmatch(value) is not None


def _receipt_rights(route):
    if route=="institutional":
        # Actual delivery-specific rights are not included in the redacted
        # receipt. Do not infer them from a generic institutional label.
        return False
    provider=ROUTE_PROVIDER.get(route)
    if provider:
        require_enabled(provider)
        if provider in RIGHTS_ENV:
            local_rights(provider,retention=True)
    return True


def _latest(items,now):
    latest={}
    seen=set()
    excluded=Counter()
    for key,task in items.items():
        if key==queue.CONTROL_KEY:
            continue
        event=task["event"]
        if key!=digest([event["event_id"],event["revision"]]):
            raise ValueError("CURRENT_RESEARCH_EVENT_IDENTITY")
        received=_time(event["received_at"])
        stored=_time(task["stored_at"]) if "stored_at" in task else None
        if stored is not None and stored<received:
            raise ValueError("CURRENT_RESEARCH_RECEIPT_CHRONOLOGY")
        if received>now or (stored is not None and stored>now):
            excluded["not_yet_visible"]+=1
            continue
        # Legacy revisions with unknown storage time may suppress an older
        # revision, but may never contribute evidence themselves.
        pair=(event["event_id"],received)
        if pair in seen:
            raise ValueError("CURRENT_RESEARCH_AMBIGUOUS_REVISION")
        seen.add(pair)
        prior=latest.get(event["event_id"])
        if prior is None or prior[0]<received:
            latest[event["event_id"]]=(received,stored,task)
    excluded["superseded_visible_revisions"]=len(seen)-len(latest)
    return latest,excluded


def _dossier_rows(report,event,completed,now,excluded):
    receipts=report.get("local_specialist_receipts",[])
    dossiers=report.get("joint_specialist_dossiers",[])
    if not isinstance(receipts,list) or len(receipts)>4 or not isinstance(dossiers,list) or len(dossiers)>4:
        raise ValueError("CURRENT_RESEARCH_DOSSIER_BUDGET")
    for d in dossiers:
        if (d["action"]!="NO_TRADE" or d["numeric_count"]!=0 or
                d["trade_authorized"] is not False or d["identity_independently_verified"] is not False
                or set(d["coverage"])!=CATEGORIES
                or any(v not in {"descriptive_reviewed","missing"} for v in d["coverage"].values())
                or d["asset_type"] not in ASSETS or _time(d["as_of"])!=_time(report["as_of"])):
            raise ValueError("CURRENT_RESEARCH_DOSSIER_CONTRACT")
        sid=validate_identity(d["security_id"],d["security_id"].startswith("SYN:"))
        if sid.startswith("SYN:")!=(event["provider"]=="synthetic"):
            raise ValueError("CURRENT_RESEARCH_FIXTURE_SCOPE")
        ids=d["research_ids"]
        if (not isinstance(ids,list) or len(ids)>4 or any(not _sha(x) for x in ids)
                or len(set(ids))!=len(ids)):
            raise ValueError("CURRENT_RESEARCH_DOSSIER_IDS")
        if ids and not _sha(d["decision_sha256"]):
            raise ValueError("CURRENT_RESEARCH_DECISION_DIGEST")
        # Match exact redacted receipts to dossier claims. Never use a bare
        # coverage flag as evidence. Block if a stored record is inconsistent.
        matched=set()
        categories=set()
        rows=[]
        for r in receipts:
            decision=r["decision_receipt"]
            if r.get("security_id")!=sid or decision.get("status")!="DESCRIPTIVE_CONTRACT_DECISION_REVIEWED":
                continue
            rid=decision.get("research_id")
            category=decision.get("category")
            if (r.get("event_subject") not in event["subjects"] or rid not in ids
                    or r.get("status")!="LOCAL_REVIEW_COMPLETED"
                    or category not in CATEGORIES or d["coverage"][category]!="descriptive_reviewed"
                    or decision.get("numeric_eligible") is not False
                    or decision.get("trade_authorized") is not False
                    or decision.get("identity_independently_verified") is not False
                    or decision.get("action")!="NO_TRADE"
                    or any(not _sha(x) for x in (r.get("task_sha256"),r.get("report_sha256"),
                                                decision.get("output_sha256"),decision.get("decision_sha256")))):
                raise ValueError("CURRENT_RESEARCH_RECEIPT_CONTRACT")
            matched.add(rid)
            categories.add(category)
            try:
                allowed=_receipt_rights(r["route"])
            except ValueError:
                allowed=False
            if not allowed:
                excluded["specialist_rights_unconfirmed"]+=1
                continue
            rows.append({"category":category,"research_id":rid,"route":r["route"],
                         "event_id":event["event_id"],"revision":event["revision"],
                         "review_completed_at":completed.isoformat(),
                         "decision_sha256":d["decision_sha256"],
                         "report_sha256":r["report_sha256"]})
        if matched!=set(ids) or categories!={k for k,v in d["coverage"].items() if v=="descriptive_reviewed"}:
            raise ValueError("CURRENT_RESEARCH_UNMATCHED_DOSSIER")
        yield sid,d["asset_type"],rows


def inspect(*,as_of,path=queue.PATH,max_review_age_seconds=86400,security_ids=None):
    now=_time(as_of)
    if now>datetime.now(UTC) or type(max_review_age_seconds) is not int or not 1<=max_review_age_seconds<=86400:
        raise ValueError("CURRENT_RESEARCH_CUTOFF_OR_AGE")
    if security_ids is not None:
        if not isinstance(security_ids,list) or not 1<=len(security_ids)<=128:
            raise ValueError("CURRENT_RESEARCH_SECURITY_SCOPE")
        for sid in security_ids:
            validate_identity(sid,isinstance(sid,str) and sid.startswith("SYN:"))
        if len(set(security_ids))!=len(security_ids):
            raise ValueError("CURRENT_RESEARCH_DUPLICATE_SECURITY")
    items,revision=queue._read_state_with_hash(path)
    latest,excluded=_latest(items,now)
    groups=defaultdict(dict)
    types=defaultdict(set)
    for _,stored,task in latest.values():
        event=task["event"]
        if stored is None:
            excluded["legacy_storage_time_unknown"]+=1
            continue
        if task["state"]!="completed":
            excluded["latest_revision_not_completed"]+=1
            continue
        report=task["report"]
        if "review_completed_at" not in report:
            excluded["legacy_completion_time_unknown"]+=1
            continue
        complete=_time(report["review_completed_at"])
        cutoff=_time(report["as_of"])
        received=_time(event["received_at"])
        if complete<max(stored,cutoff) or cutoff<received:
            raise ValueError("CURRENT_RESEARCH_COMPLETION_CHRONOLOGY")
        if complete>now:
            excluded["review_not_yet_visible"]+=1
            continue
        # Use the research cutoff as the age origin, not a newly executed
        # historical replay's completion time. Event TTL also limits reuse.
        origin=_time(event["published_at"] or event["received_at"])
        if ((now-origin).total_seconds()>event["expiry_seconds"] or
                (now-cutoff).total_seconds()>max_review_age_seconds):
            excluded["expired_event_or_review"]+=1
            continue
        try:
            raw={k:v for k,v in event.items() if k not in
                 {"received_at","event_id","revision","rights_status"}}
            if normalize(raw,received_at=event["received_at"])!=event:
                raise ValueError("CURRENT_RESEARCH_EVENT_CHANGED")
            if event["provider"] in RIGHTS_ENV:
                local_rights(event["provider"],retention=True)
        except ValueError:
            excluded["event_rights_or_validation_blocked"]+=1
            continue
        for sid,asset,rows in _dossier_rows(report,event,complete,now,excluded):
            if security_ids is not None and sid not in security_ids:
                continue
            types[sid].add(asset)
            for r in rows:
                key=(r["category"],r["research_id"])
                # Same research digest never increases evidence coverage.
                prior=groups[(sid,asset)].get(key)
                if prior is None or (r["review_completed_at"],r["event_id"])>(prior["review_completed_at"],prior["event_id"]):
                    groups[(sid,asset)][key]=r
    results=[]
    wanted=sorted(set(types)|set(security_ids or ()))
    for sid in wanted:
        assets=types[sid]
        conflict=len(assets)>1
        asset=next(iter(assets)) if len(assets)==1 else None
        rows=list(groups[(sid,asset)].values()) if asset is not None else []
        counts=Counter(r["category"] for r in rows)
        results.append({"security_id":sid,"asset_type":asset,
                        "coverage":{k:("descriptive_reviewed" if counts[k] else "missing") for k in sorted(CATEGORIES)},
                        "category_evidence_counts":dict(sorted(counts.items())),
                        "receipts":sorted(rows,key=lambda r:(r["category"],r["research_id"])),
                        "status":"CONFLICTING_ASSET_ASSOCIATIONS" if conflict else
                            "CURRENT_DESCRIPTIVE_REVIEW" if rows else "NO_CURRENT_RESEARCH",
                        "action":"NO_TRADE","numeric_count":0,"trade_authorized":False,
                        "identity_independently_verified":False})
    return {"schema":"atlaspm_current_research_v1",**FLAGS,"as_of":now.isoformat(),
            "queue_sha256":revision,"excluded":dict(excluded),"securities":results,
            "historical_point_in_time_verified":False,"current_provider_connection_verified":False,
            "scope":"retained_event_receipts_only","trade_authorized":False}


def main():
    import argparse
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of",default=datetime.now(UTC).isoformat())
    parser.add_argument("--security-id",action="append",dest="security_ids")
    parser.add_argument("--max-review-age-seconds",type=int,default=86400)
    args=parser.parse_args()
    print(json.dumps(inspect(**vars(args)),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
