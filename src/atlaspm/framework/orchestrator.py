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
    from atlaspm.research.training_autonomy import review_schedule
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
            "retrain_review_requests":review_schedule(result["monitor"],holdout_start=min(r["date"] for rows in clean.values() for r in rows)),
            "training_started":False,"model_promoted":False}


def offline_release(*, checkpoint="local_data/runs/offline_rc.continuous.json"):
    """One reproducible, credential-free integration acceptance entry.

    Built-in synthetic fixture only. Isolated fixture event queue (same
    implementation), existing persistent simulation checkpoint. No real
    data/model inference or real execution. A pause blocks new work between
    stages, but cannot interrupt an in-progress atomic local calculation.
    """
    import json,tempfile
    from pathlib import Path
    from time import perf_counter,process_time
    from atlaspm.data.market_db import private_path
    from atlaspm.framework.continuous_simulation import continuous,synthetic_fixture
    from atlaspm.framework.autonomous_cycle import run_fixture,multiasset_fixture
    from atlaspm.monitoring.api_management import inventory
    from atlaspm.research.intelligence import model_inventory,offline_model_responses,offline_joint_research
    from atlaspm.execution.oms_controls import verify_audit_chain

    start,cpu=perf_counter(),process_time()
    checks={}
    def paused():
        control=status()["control"]
        return control.get("stopped") or control.get("manual_takeover")
    def result(state,**details):
        return {**FLAGS,"status":state,"mode":"synthetic_offline_acceptance",
                "checks":checks,"network_calls":0,"broker_side_effects":False,
                "wall_seconds":perf_counter()-start,"cpu_seconds":process_time()-cpu,
                "production_performance_verified":False,**details}
    if paused():
        return result("PAUSED")
    providers=inventory()
    models=model_inventory()
    checks["credential_free_inventory"]=all(row["connection_verified"] is False for row in providers)
    data=synthetic_fixture()
    weights={"m1":2,"m2":1}
    root=private_path(Path("local_data/runs"))
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="offline-fixture-",dir=root) as temp:
        queue_path=Path(temp)/"events.json"
        count=0
        for session in data["sessions"]:
            if paused():
                return result("PAUSED")
            events=session["events"]
            added=event_queue.enqueue(events,path=queue_path)
            count+=added["added"]
            checks["event_dedup"]=event_queue.enqueue(events,path=queue_path)["added"]==0 and checks.get("event_dedup",True)
            out=tick(as_of=session["close_at"],limit=100,path=queue_path)
            checks["event_review"]=out["review"]["completed"]==len(events) and checks.get("event_review",True)
            checks["tick_idempotent"]=tick(as_of=session["close_at"],path=queue_path)["review"]["status"]=="DUPLICATE_TICK" and checks.get("tick_idempotent",True)
        checks["event_count"]=count>0
        checks["calendar"]=market_phase(data["sessions"][0]["close_at"])=="post_market"
    if paused():
        return result("PAUSED")
    model_report=offline_model_responses()
    checks["three_model_schema_fixtures"]=len(model_report["models"])==3 and all(
        m["findings"]>0 and m["actual_provider_call"] is False for m in model_report["models"])
    from atlaspm.research.financial_events import KINDS,normalize
    first=data["sessions"][0]
    stamp=first["close_at"]
    events=[e for e in first["events"] if "SYN:AAA" in e["subjects"]]
    for kind in KINDS:
        events.append(normalize({"provider":"synthetic","source_id":"SYN:joint:"+kind,"kind":kind,
            "subjects":["SYN:AAA"],"published_at":stamp,"first_available_at":stamp,"effective_at":None,
            "evidence_sha256":digest(["joint",kind]),"delivery":"file","expiry_seconds":3600},received_at=stamp))
    joint=offline_joint_research(events,[r for r in first["research"] if r["security_id"]=="SYN:AAA"],
                                 as_of=stamp,security_id="SYN:AAA",weights=weights)
    checks["joint_all_topics"]=joint["complete_topic_coverage"] and all(v=="available" for v in joint["coverage"].values())
    checks["joint_fixture_routing"]=len(joint["model_reports"])==3 and all(
        not r["actual_provider_call"] for r in joint["model_reports"])
    checks["joint_decision"]=joint["decision"]["candidates"][0]["action"]=="BUY" and not joint["trade_authorized"]
    # First run validates full prefix, second run proves identical restart.
    sim=continuous(data,weights=weights,checkpoint=checkpoint)
    repeated=continuous(data,weights=weights,checkpoint=checkpoint)
    checks["restart_identical"]=repeated["checkpoint_reused"] and sim["result_sha256"]==repeated["result_sha256"]
    replay=sim["simulation"]
    checks["continuous_flat_settled"]=sim["flat_and_settled"]
    checks["continuous_orders_audited"]=bool(replay["orders"]) and all(
        verify_audit_chain(order["events"],order["audit_root_sha256"]) for order in replay["orders"])
    checks["risk_exit"]=replay["risk_halt_latched"] and any(
        e["kind"]=="POSITION_STOP" for e in replay["position_lifecycle_audit"])
    checks["continuous_attribution"]=abs(replay["curve"][-1]["equity"]-data["initial_cash"]-
        sum(row["overnight_pnl"]+row["intraday_pnl"]-row["trade_cost"] for row in replay["curve"]))<.01
    if paused():
        return result("PAUSED")
    fixture=Path(__file__).resolve().parents[3]/"examples/unified_portfolio_synthetic.json"
    single=run_fixture(json.loads(fixture.read_text(encoding="utf-8")))
    checks["existing_oms_flat"]=single["decision"]=="SYNTHETIC_EVENT_TO_FLAT_COMPLETE"
    checks["existing_oms_attribution"]=single.get("attribution",{}).get("reconciled") is True
    with tempfile.TemporaryDirectory(prefix="offline-oms-",dir=root) as temp:
        journal_path=Path(temp)/"mixed.oms-fixture.json"
        partial=multiasset_fixture(checkpoint=journal_path,event_count=3)
        mixed=multiasset_fixture(checkpoint=journal_path)
        again=multiasset_fixture(checkpoint=journal_path)
        checks["multiasset_partial_recovery"]=any(
            0<order["filled_quantity"]<order["requested_quantity"] for order in partial["report"]["orders"])
        checks["multiasset_checkpoint_identical"]=again["checkpoint_reused"] and again["result_sha256"]==mixed["result_sha256"]
        mixed_report=mixed["report"]
        checks["multiasset_reconciled"]=mixed_report["decision"]=="PAPER_REPLAY_COMPLETED" and abs(
            mixed_report["account_replay"]["net_liquidation_reconciliation_gap"])<.01 and all(
            order["status"]=="FILLED" for order in mixed_report["orders"])
        checks["multiasset_risk_audit"]=verify_audit_chain(mixed_report["audit_events"],mixed_report["audit_root_sha256"])
        for scenario in ("cancel","unknown"):
            path=Path(temp)/(scenario+".oms-fixture.json")
            pending=multiasset_fixture(checkpoint=path,event_count=4,scenario=scenario)
            final=multiasset_fixture(checkpoint=path,scenario=scenario)
            order=final["report"]["orders"][0]
            checks["cancel_recovery_"+scenario]=(pending["report"]["orders"][0]["status"]=="CANCEL_REQUESTED" and
                order["status"]==("CANCELED" if scenario=="cancel" else "UNKNOWN") and order["filled_quantity"]==4 and
                abs(final["report"]["account_replay"]["net_liquidation_reconciliation_gap"])<.01 and
                (scenario!="unknown" or final["report"]["decision"]=="HALT_RECONCILIATION"))

    if paused():
        return result("PAUSED")
    # Link fixed prediction observations to actual synthetic next-close
    # outcomes only after their label dates. Baseline uses past returns.
    streams={m:[] for m in ("historical_mean","m1","m2")}
    sessions=data["sessions"]
    for idx,session in enumerate(sessions[:-1]):
        after=sessions[idx+1]
        by_symbol={}
        for row in session["research"]:
            by_symbol.setdefault(row["security_id"],{})[row["model_version"]]=row["expected_return"]
        for sid,values in sorted(by_symbol.items()):
            if set(values)!={"m1","m2"}:
                continue
            sym=sid[4:]
            past=[sessions[j]["close"][sym]/sessions[j-1]["close"][sym]-1 for j in range(1,idx+1)]
            values["historical_mean"]=sum(past)/len(past) if past else 0.
            base={"date":session["date"],"security_id":sid,"label_end_date":after["date"],
                  "actual":after["close"][sym]/session["close"][sym]-1}
            for model,prediction in values.items():
                streams[model].append({**base,"prediction":prediction})
    learning=learning_review(streams,as_of_date=sessions[-1]["date"],window_dates=3,minimum_dates=2)
    checks["learning_no_promotion"]=learning["model_promoted"] is False and learning["training_started"] is False
    checks["learning_outcome_links"]=all(streams.values())
    return result("OFFLINE_FIXTURE_PASS" if all(checks.values()) else "OFFLINE_FIXTURE_FAIL",
                  providers=providers,model_inventory=models,model_fixture=model_report,
                  synthetic_simulation={"sessions":sim["sessions"],"orders":len(replay["orders"]),
                      "flat_and_settled":sim["flat_and_settled"],"ending_equity":replay["curve"][-1]["equity"],
                      "total_cost":replay["total_cost"],"result_sha256":sim["result_sha256"]},
                  single_cycle_decision=single["decision"],learning=learning,
                  joint_fixture={"coverage":joint["coverage"],"model_reports":joint["model_reports"],"model_outputs_are_fixtures":True},
                  mixed_asset_fixture={"decision":mixed_report["decision"],"orders":len(mixed_report["orders"]),
                                       "result_sha256":mixed["result_sha256"],"broker_verified":False},
                  historical_data_certified=False,real_api_connected=False,
                  release_candidate_approved=False)


def main():
    import argparse,json,time
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("status","run","stop","takeover","resume","offline","research"))
    parser.add_argument("--cycles",type=int,default=1)
    parser.add_argument("--interval",type=int,default=60)
    parser.add_argument("--checkpoint",default="local_data/runs/offline_rc.continuous.json")
    parser.add_argument("--file",help="Local research task JSON under local_data")
    args=parser.parse_args()
    if not 1<=args.cycles<=100 or not 1<=args.interval<=300:
        parser.error("Invalid worker bounds")
    if args.action=="research":
        from pathlib import Path
        from atlaspm.data.market_db import private_path
        from atlaspm.research.intelligence import local_specialist_tasks
        if not args.file:
            parser.error("research requires --file")
        control=status()["control"]
        if control.get("stopped") or control.get("manual_takeover"):
            print(json.dumps({**FLAGS,"status":"PAUSED"}))
            return 1
        path=private_path(Path(args.file),exists=True)
        with path.open("rb") as handle:
            raw=handle.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:
            raise ValueError("LOCAL_RESEARCH_FILE_LIMIT")
        data=json.loads(raw)
        out=local_specialist_tasks(data["tasks"],as_of=data["as_of"])
        out["tasks"]=[{k:v for k,v in row.items() if k!="report"} for row in out["tasks"]]
        print(json.dumps(out,indent=2,allow_nan=False))
        return 0
    if args.action=="offline":
        out=offline_release(checkpoint=args.checkpoint)
        print(json.dumps(out,indent=2,allow_nan=False))
        return 0 if out["status"]=="OFFLINE_FIXTURE_PASS" else 1
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
    raise SystemExit(main())
