"""One-command offline acceptance smoke for the synthetic research chain."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atlaspm.research.training_benchmark import sessions
from atlaspm.research.strategy_closed_loop import run
from atlaspm.research.training_registry import FLAGS
from atlaspm.execution.synthetic_oms_receipts import execute
from atlaspm.execution.oms_controls import verify_audit_chain
from atlaspm.framework.trained_cycle import run_trained_fixture


def smoke(example):
    panel={"mode":"synthetic","synthetic_fixture":True,
           "feature_names":["momentum","variation"],"sessions":list(sessions(85,4))}
    marks=[]
    for session in panel["sessions"]:
        prices={a["security_id"][4:]:a["close"] for a in session["assets"]}
        marks.append({"date":session["date"],"open_at":session["date"]+"T09:30:00-05:00",
                      "close_at":session["close_at"],"source":"synthetic","open":dict(prices),"close":prices})
    market={"mode":"synthetic","synthetic_fixture":True,"sessions":marks,
            "sectors":{s:"synthetic" for s in marks[0]["close"]}}
    outcome=run(panel,market,model_config={"minimum_train_sessions":10,"test_sessions":5},
        model_candidates=[{"model":"historical_mean"},{"model":"ridge","ridge_alpha":.1}],
        model_selection_end=31,strategy_holdout_start=56,
        strategies=[{"top_k":1,"rebalance_sessions":1},{"top_k":2,"rebalance_sessions":5}],
        execution={"initial_cash":10000,"fee_bps":1,"slippage_bps":1,
                   "risk_policy":{"max_drawdown":.25,"max_stress_loss_fraction":.25}},
        leverage_scenarios=[{"shock":.2,"maintenance_fraction":.25,"initial_fraction":.5,
                             "apr":.06,"days":30,"min_excess_fraction":.1,"max_equity_loss":.5}])
    if outcome["status"]!="SYNTHETIC_CASH_CLOSED_LOOP_COMPLETE":
        raise ValueError("SMOKE_STRATEGY_FAILED:"+outcome["status"])
    holdout=outcome["holdout"]
    fixture=json.loads(Path(example).read_text(encoding="utf-8"))
    fixture["paper"]["fill_fraction"]=.5
    fixture["paper"]["duplicate_last_event"]=True
    root=Path("local_data")
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="synthetic-acceptance-",dir=root) as tmp:
        db=Path(tmp)/"receipts.sqlite3"
        first=execute(db,"SYN:acceptance",fixture)
        second=execute(db,"SYN:acceptance",fixture)
        trained_fixture=json.loads(Path(example).read_text(encoding="utf-8"))
        trained=run_trained_fixture(trained_fixture,registry_path=Path(tmp)/"trained.sqlite3")
    paper=first["report"]
    checks={
        "model_and_strategy_selected":outcome["holdout_evaluated"] is True and holdout["trade_count"]>0,
        "next_open_execution":all(t["signal_date"]<t["date"] for t in holdout["trades"]),
        "cash_only":all(row["settled_cash"]>=-1e-8 for row in holdout["curve"]),
        "risk_chain":verify_audit_chain(holdout["risk_audit"],holdout["risk_audit_root"]),
        "financing_is_hypothetical":outcome["hypothetical_financing"]["broker_margin_verified"] is False,
        "institutional_oms":paper["decision"]=="SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE",
        "partial_fill_reconciled":bool(paper.get("reconciliation_checks")) and all(paper["reconciliation_checks"].values()),
        "duplicate_event_ignored":paper.get("paper_oms",{}).get("duplicate_events_ignored")==1,
        "receipt_reused":first["reused"] is False and second["reused"] is True and first["report_sha256"]==second["report_sha256"],
        "trained_model_to_matured_feedback":trained["decision"]=="SYNTHETIC_TRAINED_EVENT_TO_FEEDBACK_COMPLETE",
        "trained_model_actual_inference":trained["trained_model_used"] is True and len(trained["models"])==2,
        "trained_cycle_attribution":trained.get("cycle",{}).get("attribution",{}).get("reconciled") is True,
        "trained_cycle_feedback":bool(trained.get("learning",{}).get("prediction_errors")),
        "all_permissions_blocked":all(trained.get(k) is v for k,v in FLAGS.items()) and all(outcome.get(k) is v for k,v in FLAGS.items())
            and paper["live_execution_enabled"] is False and paper["broker_order_submission_enabled"] is False,
    }
    return {"status":"SYNTHETIC_SMOKE_PASS" if all(checks.values()) else "SYNTHETIC_SMOKE_FAIL",
            "checks":checks,"selected_model":outcome["selection"]["selected_model"],
            "selected_strategy":outcome["selected_strategy"],"holdout_trade_count":holdout["trade_count"],
            "holdout_statistics":holdout["statistics"],"oms_decision":paper["decision"],
            "trained_cycle_decision":trained["decision"],
            "trained_cycle_observation_interval_days":trained["fixture_observation_interval_days"],
            "trained_cycle_retraining_review_required":trained.get("learning",{}).get("retraining_review_required"),
            "production_ready":False,"real_data_training_ready":False,
            "blockers":["REAL_DATA_CERTIFICATION_REQUIRED","BROKER_MARGIN_VERIFICATION_REQUIRED",
                        "LIVE_ORDER_RECONCILIATION_AND_APPROVAL_REQUIRED"],**FLAGS}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--example",type=Path,default=Path("examples/unified_portfolio_synthetic.json"))
    args=p.parse_args()
    report=smoke(args.example)
    print(json.dumps(report,indent=2,allow_nan=False))
    return 0 if report["status"]=="SYNTHETIC_SMOKE_PASS" else 1


if __name__=="__main__":
    raise SystemExit(main())
