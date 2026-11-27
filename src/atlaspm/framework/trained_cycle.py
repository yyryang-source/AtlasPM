"""Actual numeric training through central decisions, OMS, exit and feedback.

Deterministic annual-observation fixtures only. One trainer label is exactly
365 calendar days, matching the existing annual portfolio return contract.
These artificial paths exercise software; they are not market data, calibration
proof, a strategy backtest, or an operational model admission.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from statistics import fmean

from atlaspm.engine.autonomous_decision import portfolio_cycle
from atlaspm.engine.contracts import Account, finite, timestamp
from atlaspm.framework.autonomous_cycle import complete_synthetic_exit
from atlaspm.research.training_artifacts import ModelArtifacts, predict_snapshot
from atlaspm.research.training_engine import train
from atlaspm.research.financial_events import FLAGS as EVENT_FLAGS
from atlaspm.research.training_paper_feedback import record_paper_feedback
from atlaspm.research.training_registry import FLAGS, digest, local_path, register


def _annual_panel(account, now, offset):
    """Disjoint historical fixture windows; features precede their labels."""
    sessions=[]
    for index in range(32):
        observed=now-timedelta(days=365*(offset+32-index))
        stamp=observed.isoformat()
        assets=[]
        for number,holding in enumerate(account.holdings):
            growth=.10+.01*(number%4)
            assets.append({"security_id":"SYN:"+holding.symbol,"source":"synthetic",
                "observed_at":stamp,"available_at":stamp,
                "close":holding.price*(1+growth)**(index-31),
                "features":{"growth":growth,"cycle":float(index%3)}})
        sessions.append({"date":observed.date().isoformat(),"close_at":stamp,"assets":assets})
    return {"mode":"synthetic","synthetic_fixture":True,
            "feature_names":["growth","cycle"],"sessions":sessions}


def run_trained_fixture(payload, *, registry_path, exit_price_fraction=.95):
    """Train, reload and infer; do not substitute caller forecasts or weights.

    All persistence reuses the existing research registry, numeric-artifact
    tables and matured Paper-feedback table in one ignored local SQLite file.
    Repeating identical inputs reuses content-addressed records. Actual model
    outputs and matured residuals determine the central decision; a failed
    numeric/risk/OMS gate remains blocked, without a fabricated fallback.
    """
    if (not isinstance(payload,dict) or payload.get("mode")!="synthetic"
        or payload.get("synthetic_fixture") is not True
        or any(k in payload and payload[k] is not v for k,v in {**EVENT_FLAGS,**FLAGS}.items())):
        raise ValueError("TRAINED_CYCLE_SYNTHETIC_SCOPE_REQUIRED")
    account=Account.from_payload(payload["account"])
    if account.kind!="cash" or not account.account_id.startswith("SYN-") or not 2<=len(account.holdings)<=20:
        raise ValueError("TRAINED_CYCLE_BOUNDED_SYNTHETIC_CASH_REQUIRED")
    fraction=finite(exit_price_fraction,"exit_price_fraction",minimum=.01)
    if fraction>2:
        raise ValueError("TRAINED_CYCLE_MARK_RANGE")
    path=local_path(registry_path)
    now=timestamp(payload["as_of"],"as_of")
    if now.year<1900:
        raise ValueError("TRAINED_CYCLE_FIXTURE_DATE_RANGE")
    current={"mode":"synthetic","synthetic_fixture":True,
        "feature_names":["growth","cycle"],"sessions":[{
        "date":now.date().isoformat(),"close_at":now.isoformat(),"assets":[{
            "security_id":"SYN:"+h.symbol,"source":"synthetic",
            "observed_at":now.isoformat(),"available_at":now.isoformat(),"close":h.price,
            "features":{"growth":.10+.01*(i%4),"cycle":0.}}
            for i,h in enumerate(account.holdings)]}]}
    store=ModelArtifacts(path)
    rows=[]
    models=[]
    weights={}
    for offset in (1,34):
        panel=_annual_panel(account,now,offset)
        settings={"horizon":1,"minimum_train_sessions":8,"test_sessions":4,
                  "embargo_sessions":1,"models":["ridge"],"ridge_alpha":.1}
        fitted=train(panel,model_artifact_path=str(path),**settings)
        run_id=register(path,settings,fitted)
        artifact_id=fitted["folds"][-1]["model_artifact_ids"]["ridge"]
        artifact=store.load(artifact_id)
        inferred=predict_snapshot(artifact,current)
        matured=[r for r in fitted["predictions"]["ridge"]
                 if r["label_end_date"]<now.date().isoformat() and r["actual"] is not None]
        if len({r["date"] for r in matured})<10:
            raise ValueError("TRAINED_CYCLE_INSUFFICIENT_MATURED_RESIDUALS")
        # A conservative empirical error envelope, not a calibrated probability.
        radius=max(.005,max(abs(r["prediction"]-r["actual"]) for r in matured))
        version="ridge:"+artifact_id
        weights[version]=1.
        evidence={"artifact_id":artifact_id,"training_run_id":run_id,
                  "training_dataset_sha256":fitted["dataset_sha256"],
                  "inference_sha256":digest(inferred),"matured_residual_count":len(matured),
                  "historical_window_start":panel["sessions"][0]["date"],
                  "historical_window_end":panel["sessions"][-1]["date"],
                  "forecast_horizon_calendar_days":365,"uncertainty_radius":radius,
                  "statistical_independence_verified":False}
        models.append(evidence)
        for prediction in inferred["predictions"]:
            rows.append({"security_id":prediction["security_id"],"asset_type":"stock",
                "category":"factor","kind":"quantitative_prediction","source":"synthetic",
                "model_version":version,"evidence":[{"sha256":digest(evidence),
                    "available_at":now.isoformat(),"received_at":now.isoformat(),
                    "source":"synthetic","rights_status":"synthetic_test_only"}],
                "generated_at":now.isoformat(),"expires_at":(now+timedelta(minutes=30)).isoformat(),
                "horizon_days":365,"expected_return":prediction["prediction"],
                "uncertainty_radius":radius,"risk_factors":["artificial annual observation fixture",
                    "historical residual envelope is not calibrated future uncertainty"],
                "invalidation":["forecast horizon reached","independent risk veto"],"synthetic_fixture":True})
    liquidity={h.symbol:{"daily_dollar_volume":1e7,"spread_bps":2,
                         "observed_at":now.isoformat(),"source":"synthetic"} for h in account.holdings}
    entry=portfolio_cycle(deepcopy(payload),rows,weights=weights,cost_fraction=.03,liquidity=liquidity)
    lineage=digest([m["training_dataset_sha256"] for m in models])
    base={**EVENT_FLAGS,**FLAGS,"dataset_sha256":lineage,"models":models,"research_rows":rows,
          "trained_model_used":True,"broker_side_effects":False,
          "fixture_observation_interval_days":365,"model_promoted":False}
    if entry["decision"]!="SYNTHETIC_ENTRY_REVIEW_COMPLETE":
        return {**base,"decision":"TRAINED_CYCLE_ENTRY_BLOCKED","entry":entry}
    end=now+timedelta(days=365)
    marks={h.symbol:h.price*fraction for h in account.holdings}
    closed=complete_synthetic_exit(payload,entry,events=models,marks=marks,exit_at=end,
                                  exit_reason="SYNTHETIC_FORECAST_HORIZON_REACHED",trained_model_used=True)
    if closed["decision"]!="SYNTHETIC_EVENT_TO_FLAT_COMPLETE":
        return {**base,"decision":"TRAINED_CYCLE_EXIT_BLOCKED","cycle":closed}
    study=register(path,{"purpose":"synthetic_trained_cycle","horizon_days":365},
        {**base,"status":"SYNTHETIC_TRAINED_CYCLE_INFERENCE","decision":entry["decision"]},kind="experiment")
    decision=register(path,{"as_of":now.isoformat(),"maturity_date":end.date().isoformat(),
        "research_run_id":study,"account_id":account.account_id,"annual_margin_rate":0.},
        {**FLAGS,"dataset_sha256":lineage,"status":"SYNTHETIC_PAPER_DECISION",
         "decision":"SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE","portfolio":entry["portfolio"]},kind="experiment")
    held=entry["portfolio"]["paper_oms"]["account_replay"]["positions"]
    feedback=record_paper_feedback(registry_path=path,decision_id=decision,
        observation={"mode":"synthetic","synthetic_fixture":True,"observed_at":end.isoformat(),
                     "available_at":end.isoformat(),"marks":{p["identifier"]:marks[p["identifier"]] for p in held}},
        as_of=end.isoformat())
    exit_opening=closed["exit_oms"]["account_replay"]["opening_net_liquidation"]
    if abs(feedback["ending_equity"]-exit_opening)>.01:
        raise ValueError("TRAINED_CYCLE_FEEDBACK_EXIT_EQUITY_MISMATCH")
    errors=[{"security_id":row["security_id"],"model_version":row["model_version"],
             "expected_return":row["expected_return"],"observed_return":fraction-1,
             "prediction_error":fraction-1-row["expected_return"],
             "outside_empirical_envelope":abs(fraction-1-row["expected_return"])>row["uncertainty_radius"]}
            for row in rows]
    review=any(e["outside_empirical_envelope"] for e in errors)
    learning={"prediction_errors":errors,"mean_absolute_error":fmean(abs(e["prediction_error"]) for e in errors),
              "matured_portfolio_feedback":feedback,"retraining_review_required":review,
              "research_task":"REVIEW_MODEL_DECAY_AND_NEW_OOS_EXPERIMENT" if review else None,
              "automatic_retraining":False,"model_promoted":False,
              "evaluation_basis":"365_day_asset_return_and_separate_marked_portfolio_feedback"}
    result={**base,"decision":"SYNTHETIC_TRAINED_EVENT_TO_FEEDBACK_COMPLETE",
            "cycle":closed,"learning":learning,"paper_decision_id":decision,"research_run_id":study,
            "maturity_at":end.isoformat(),"exit_settlement_at":(end+timedelta(days=1)).isoformat()}
    result["cycle_id"]=register(path,{"input_sha256":digest(payload),"exit_price_fraction":fraction},result,kind="experiment")
    return result


def main():
    import argparse
    import json
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture",type=Path,default=Path("examples/unified_portfolio_synthetic.json"))
    parser.add_argument("--registry",default="local_data/trained_cycle.sqlite3")
    args=parser.parse_args()
    if args.fixture.stat().st_size>1024*1024:
        raise ValueError("TRAINED_CYCLE_FIXTURE_SIZE_LIMIT")
    report=run_trained_fixture(json.loads(args.fixture.read_text(encoding="utf-8")),registry_path=args.registry)
    print(json.dumps({k:v for k,v in report.items() if k not in {"cycle","research_rows"}},indent=2,allow_nan=False))
    return 0 if report["decision"]=="SYNTHETIC_TRAINED_EVENT_TO_FEEDBACK_COMPLETE" else 1


if __name__=="__main__":
    raise SystemExit(main())
