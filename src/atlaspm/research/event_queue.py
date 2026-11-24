"""Bounded resumable research-event queue using the existing local runs area.

One atomic state file, one writer lock. Tasks are local deterministic review
only; no paid network requests, credentials, or broker side effects.
"""
from __future__ import annotations
from datetime import UTC,datetime
import json
import os
from pathlib import Path
from atlaspm.data.market_db import private_path
from atlaspm.data.sec_download import _atomic_write
from atlaspm.research.financial_events import normalize,review,FLAGS,KINDS
from atlaspm.research.training_registry import digest

PATH=Path("local_data/runs/financial_event_queue.json")
MAX_BYTES=8*1024*1024
MAX_ITEMS=1000
CONTROL_KEY="__system_control__"


def _read_state(path):
    p=private_path(Path(path))
    if not p.exists():
        return {}
    with p.open("rb") as f:
        raw=f.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:
        raise ValueError("EVENT_QUEUE_BYTE_BUDGET")
    state=json.loads(raw)
    if not isinstance(state,dict) or set(state)!={"items","sha256"} or digest(state["items"])!=state["sha256"]:
        raise ValueError("EVENT_QUEUE_INTEGRITY_FAILURE")
    items=state["items"]
    if not isinstance(items,dict) or len(items)>MAX_ITEMS:
        raise ValueError("EVENT_QUEUE_CAPACITY")
    return items


def _transaction(path, operation):
    p=private_path(Path(path))
    p.parent.mkdir(parents=True,exist_ok=True)
    lock=p.with_suffix(".lock")
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    try:
        items=_read_state(p)
        result=operation(items)
        body=json.dumps({"items":items,"sha256":digest(items)},sort_keys=True,
                        ensure_ascii=False,allow_nan=False).encode()
        if len(items)>MAX_ITEMS or len(body)>MAX_BYTES:
            raise ValueError("EVENT_QUEUE_CAPACITY")
        _atomic_write(p,lambda f:f.write(body))
        return result
    finally:
        lock.unlink(missing_ok=True)


def enqueue(events, *, path=PATH):
    if not isinstance(events,list) or len(events)>MAX_ITEMS:
        raise ValueError("EVENT_BATCH_CAPACITY")
    checked=[]
    for e in events:
        raw={k:v for k,v in e.items() if k not in {"received_at","event_id","revision","rights_status"}}
        item=normalize(raw,received_at=e["received_at"])
        if item!=e:
            raise ValueError("EVENT_QUEUE_UNVALIDATED_EVENT")
        # Queue persists licensed metadata; retention required for restricted sources.
        from atlaspm.data.provider_policy import RIGHTS_ENV,local_rights
        if item["provider"] in RIGHTS_ENV:
            local_rights(item["provider"],retention=True)
        checked.append(item)
    def add(items):
        count=0
        for event in checked:
            key=digest([event["event_id"],event["revision"]])
            if key in items:
                continue
            items[key]={"event":event,"state":"pending","attempts":0,"report":None}
            count+=1
        return {**FLAGS,"added":count,"retained":len(items)}
    return _transaction(path,add)


def drain(*, as_of, holdings=(), limit=20,path=PATH, schedule=None, local_bindings=None):
    """At most limit deterministic reviews per call; atomic commit or retry.
    Process restart resumes pending items. No claim of exactly-once external
    delivery; no external side effect is allowed here.
    """
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError("EVENT_DRAIN_LIMIT")
    from atlaspm.research.financial_events import _time
    from atlaspm.research.intelligence import validate_local_bindings
    bindings=validate_local_bindings([] if local_bindings is None else local_bindings)
    cutoff=_time(as_of)
    if cutoff>datetime.now(UTC):
        raise ValueError("FUTURE_EVENT_DRAIN")
    def process(items):
        control=items.get(CONTROL_KEY,{})
        if control.get("stopped") or control.get("manual_takeover"):
            return {**FLAGS,"status":"PAUSED","completed":0,"pending":sum(t.get("state")=="pending" for t in items.values())}
        if schedule is not None and control.get("last_tick")==schedule["tick_id"]:
            return {**FLAGS,"status":"DUPLICATE_TICK","completed":0,"pending":sum(t.get("state")=="pending" for t in items.values())}
        if schedule is not None and control.get("last_as_of") and cutoff<_time(control["last_as_of"]):
            raise ValueError("NONMONOTONIC_SYSTEM_TICK")
        visible=[(k,v) for k,v in items.items() if k!=CONTROL_KEY and _time(v["event"]["received_at"])<=cutoff]
        latest={}
        for key,task in visible:
            event=task["event"]
            prior=latest.get(event["event_id"])
            if prior is not None and prior[1]["event"]["received_at"]==event["received_at"] and prior[0]!=key:
                raise ValueError("AMBIGUOUS_EVENT_REVISION_RECEIPT")
            if prior is None or prior[1]["event"]["received_at"]<event["received_at"]:
                latest[event["event_id"]]=(key,task)
        superseded=0
        for key,task in visible:
            if task["state"]=="pending" and latest[task["event"]["event_id"]][0]!=key:
                task["state"]="superseded"
                superseded+=1
        pending=[(k,v) for k,v in visible if v["state"]=="pending"]
        pending.sort(key=lambda kv:(-KINDS[kv[1]["event"]["kind"]],kv[1]["event"]["received_at"],kv[0]))
        complete=failed=0
        local_calls=0
        for key,task in pending[:limit]:
            event=task["event"]
            matches=[b for b in bindings if b["kind"]==event["kind"] and b["subject"] in event["subjects"]]
            if local_calls+len(matches)>4:
                break  # keep the remaining tasks pending for the next tick
            task["attempts"]+=1
            try:
                from atlaspm.data.provider_policy import RIGHTS_ENV,local_rights
                provider=task["event"]["provider"]
                if provider in RIGHTS_ENV:
                    local_rights(provider,retention=True)
                report=review([task["event"]],as_of=as_of,holdings=holdings)
                from atlaspm.research.intelligence import event_research
                research=event_research(report)
                local_receipts=[]
                if matches and report["events"] and not report["events"][0]["expired"]:
                    from atlaspm.research.intelligence import local_specialist_tasks
                    local_calls+=len(matches)
                    out=local_specialist_tasks([b["task"] for b in matches],as_of=as_of)
                    local_receipts=[{k:v for k,v in r.items() if k!="report"} for r in out["tasks"]]
                task["report"]={"research_requests":report["research_requests"],
                                "risk_review_requests":report["risk_review_requests"],
                                "review_sha256":digest(report),"as_of":as_of,
                                "specialist_tasks":research["research_tasks"],
                                "blocked_specialist_tasks":research["blocked_tasks"],
                                "local_specialist_receipts":local_receipts}
                task["state"]="completed"
                complete+=1
            except (ValueError,TypeError,KeyError):
                task["report"]=None
                if task["attempts"]>=3:
                    task["state"]="failed"
                failed+=1
        result={**FLAGS,"status":"REVIEWED","completed":complete,"failed_attempts":failed,"superseded":superseded,
                "pending":sum(t.get("state")=="pending" for t in items.values()),
                "quarantined":sum(t.get("state")=="failed" for t in items.values()),"local_research_calls":local_calls}
        if schedule is not None:
            items[CONTROL_KEY]={**control,"last_tick":schedule["tick_id"],"last_as_of":cutoff.isoformat(),
                                "phase":schedule["phase"],"last_result":result}
        return result
    return _transaction(path,process)


def main():
    from argparse import ArgumentParser
    import time
    parser=ArgumentParser(description=__doc__)
    parser.add_argument("--input",help="Explicit normalized event file to enqueue")
    parser.add_argument("--cycles",type=int,default=1)
    parser.add_argument("--interval",type=int,default=5)
    parser.add_argument("--limit",type=int,default=20)
    args=parser.parse_args()
    if not 1<=args.cycles<=100 or not 1<=args.interval<=300:
        parser.error("Invalid bounded worker duration")
    if args.input:
        from atlaspm.research.financial_events import import_events
        print(json.dumps(enqueue(import_events(args.input))))
    for index in range(args.cycles):
        print(json.dumps(drain(as_of=datetime.now(UTC).isoformat(),limit=args.limit)))
        if index+1<args.cycles:
            time.sleep(args.interval)


if __name__=="__main__":
    main()
