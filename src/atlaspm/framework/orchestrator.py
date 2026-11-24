"""Local autonomous review control using the existing financial event queue.

No automatic network, model training, order calls or background process.
A bounded foreground worker can be invoked by an operator's local scheduler.
"""
from __future__ import annotations
from datetime import UTC,datetime
from atlaspm.research import event_queue
from atlaspm.research.financial_events import FLAGS,_time
from atlaspm.research.training_registry import digest


def market_phase(as_of):
    """Official-calendar package schedule, including holidays and early closes."""
    import pandas_market_calendars as mcal
    from zoneinfo import ZoneInfo
    now=_time(as_of)
    day=now.astimezone(ZoneInfo("America/New_York")).date()
    schedule=mcal.get_calendar("NYSE").schedule(start_date=day,end_date=day)
    if schedule.empty:
        return "closed"
    opening=schedule.iloc[0]["market_open"].to_pydatetime()
    closing=schedule.iloc[0]["market_close"].to_pydatetime()
    if now<opening:
        return "pre_market"
    if now<closing:
        return "market"
    return "post_market"


def status(*, path=event_queue.PATH):
    items=event_queue._read_state(path)
    control=items.get(event_queue.CONTROL_KEY,{})
    counts={}
    for key,item in items.items():
        if key!=event_queue.CONTROL_KEY:
            state=item["state"]
            counts[state]=counts.get(state,0)+1
    return {**FLAGS,"control":control,"queue_counts":counts,
            "network_tasks_enabled":False,"formal_training_enabled":False,
            "broker_tasks_enabled":False,"max_concurrency":1}


def set_control(action, *, path=event_queue.PATH):
    """Explicit local operator action; resume never grants broker permissions."""
    if action not in {"stop","takeover","resume"}:
        raise ValueError("INVALID_SYSTEM_CONTROL")
    def change(items):
        old=items.get(event_queue.CONTROL_KEY,{})
        items[event_queue.CONTROL_KEY]={**old,"stopped":action=="stop",
            "manual_takeover":action=="takeover","operator_action":action,
            "changed_at":datetime.now(UTC).isoformat()}
        return {**FLAGS,"control":items[event_queue.CONTROL_KEY]}
    return event_queue._transaction(path,change)


def tick(*, as_of, holdings=(),limit=20,path=event_queue.PATH):
    cutoff=_time(as_of)
    if cutoff>datetime.now(UTC):
        raise ValueError("FUTURE_SYSTEM_TICK")
    phase=market_phase(as_of)
    # One distinct tick per minute. Same tick cannot duplicate local reviews.
    minute=cutoff.replace(second=0,microsecond=0)
    schedule={"phase":phase,"tick_id":digest([minute.isoformat(),phase])}
    result=event_queue.drain(as_of=as_of,holdings=holdings,limit=limit,path=path,schedule=schedule)
    return {**FLAGS,"phase":phase,"tick":schedule["tick_id"],"review":result,
            "scheduled_tasks":{"financial_event_review":result["status"],
                "market_data":"DISABLED_REAL_CONNECTION_UNVERIFIED",
                "external_ai":"DISABLED_OPERATOR_TASK_REQUIRED",
                "investment_execution":"DISABLED","formal_training":"DISABLED"},
            "broker_side_effects":False}


def learning_review(streams, *, as_of_date, window_dates=30, minimum_dates=10):
    """Local evaluation only: existing chronological ensemble and monitor.

    No training job is started and no registry champion is modified.
    Outcome labels at/after the cutoff are masked before model evaluation.
    """
    from datetime import date
    from atlaspm.research.signal_fusion import historical_diagnostics,matured_correlations
    cutoff=date.fromisoformat(as_of_date)
    if (not isinstance(streams,dict) or not 2<=len(streams)<=8 or
        any(not isinstance(rows,list) or len(rows)>10000 for rows in streams.values())):
        raise ValueError("BOUNDED_LEARNING_REVIEW_REQUIRED")
    clean={}
    for name,rows in streams.items():
        clean[name]=[{**r,"actual":r["actual"] if date.fromisoformat(r["label_end_date"])<cutoff else None}
                     for r in rows if date.fromisoformat(r["date"])<=cutoff]
    result=historical_diagnostics(clean,window_dates=window_dates,minimum_dates=minimum_dates)
    correlations=matured_correlations(clean,as_of_date=as_of_date,minimum_dates=minimum_dates)
    return {**FLAGS,"as_of_date":as_of_date,"diagnostics":result,"correlations":correlations,
            "retrain_task":"INDEPENDENT_SYNTHETIC_RESEARCH_REVIEW_REQUIRED",
            "training_started":False,"model_promoted":False}


def main():
    import argparse,json,time
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("status","run","stop","takeover","resume"))
    parser.add_argument("--cycles",type=int,default=1)
    parser.add_argument("--interval",type=int,default=60)
    args=parser.parse_args()
    if not 1<=args.cycles<=100 or not 1<=args.interval<=300:
        parser.error("Invalid worker bounds")
    if args.action=="status":
        print(json.dumps(status(),indent=2))
    elif args.action!="run":
        print(json.dumps(set_control(args.action),indent=2))
    else:
        for i in range(args.cycles):
            result=tick(as_of=datetime.now(UTC).isoformat())
            print(json.dumps(result))
            if result["review"]["status"]=="PAUSED":
                break
            if i+1<args.cycles:
                time.sleep(args.interval)


if __name__=="__main__":
    main()
