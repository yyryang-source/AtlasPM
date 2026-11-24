"""Adaptive synthetic paper decision adapter; no broker API imports.

This consumes an independently evaluated local research run and chronological
forecast streams. It delegates allocation, preflight, OMS and reconciliation
to Unified Portfolio Engine. A NO_TRADE decision is not a cash liquidation.
"""
from __future__ import annotations

from copy import deepcopy
import argparse
import json
from pathlib import Path
from atlaspm.engine.contracts import timestamp, finite
from atlaspm.engine.unified import run_unified_portfolio
from atlaspm.research.training_registry import FLAGS, load, local_path, register
from atlaspm.research.training_monitor import monitor_predictions
from atlaspm.research.training_autonomous import _code_version
from atlaspm.research.training_paper_feedback import matured_feedback, record_paper_feedback


def paper_decision(payload, streams, *, registry_path, run_id,
                   minimum_dates=10, window_dates=30, max_interval_width=.05):
    def no_trade(reason, **extra):
        return dict(decision="NO_TRADE",reason=reason,**extra,**FLAGS)
    if payload.get("mode")!="synthetic" or payload.get("synthetic_fixture") is not True:
        return no_trade("SYNTHETIC_PAPER_ONLY")
    if any(k in payload and payload[k] is not v for k,v in FLAGS.items()):
        return no_trade("PERMISSION_OVERRIDE_DENIED")
    try:
        record=load(local_path(registry_path),run_id)
        report=record["report"]
        config=record["config"]
        selected=report.get("selected_research_strategy")
        if (record["kind"]!="experiment" or report.get("status")!="CHALLENGER_HOLDOUT_PASS_RESEARCH_ONLY"
            or report.get("holdout_consumed") is not True or not isinstance(selected,dict)
            or config.get("code_sha256")!=_code_version()):
            return no_trade("NO_CURRENT_QUALIFIED_RESEARCH_CHALLENGER")
        if not payload["account"]["account_id"].startswith("SYN-"):
            return no_trade("SYNTHETIC_ACCOUNT_ID_REQUIRED")
        model=selected["model"]
        as_of=timestamp(payload["as_of"],"as_of")
        day=as_of.date().isoformat()
        if day<=config["holdout_end"]:
            return no_trade("DECISION_MUST_FOLLOW_FROZEN_HOLDOUT")
        for field in ("market_data_available_at","history_available_at"):
            if timestamp(payload[field],field)>as_of:
                return no_trade("MARKET_OR_HISTORY_LOOKAHEAD")
        if set(streams)!={model,"historical_mean"}:
            return no_trade("FORECAST_MODELS_DO_NOT_MATCH_RESEARCH")
        clean={}
        for name,rows in streams.items():
            clean[name]=[]
            for r in rows:
                if r["date"]>day:
                    return no_trade("FUTURE_FORECAST_PRESENT")
                if not r["security_id"].startswith("SYN:"):
                    return no_trade("UNVERIFIED_FORECAST_IDENTITY")
                # Never let current/unmatured outcome values enter the policy.
                clean[name].append({**r,"actual":r["actual"] if r["label_end_date"]<day else None})
        report_monitor=monitor_predictions(clean,minimum_dates=minimum_dates,
                                           window_dates=window_dates)
        latest=report_monitor["daily"][-1]
        if latest["date"]!=day or latest["champion"]!=model:
            return no_trade("BASELINE_FALLBACK_OR_NO_CURRENT_SIGNAL",monitor=latest)
        stats=latest["models"][model]
        radius=stats["interval_half_width"]
        width=finite(max_interval_width,"max_interval_width",minimum=.000001)
        if radius is None or radius>=width or stats["drift_alert"] is True:
            return no_trade("UNCERTAINTY_OR_DRIFT_LIMIT",monitor=latest)
        forecasts={r["security_id"]:r["prediction"] for r in clean[model] if r["date"]==day}
        signals=payload["signals"]
        if len(signals)!=len(forecasts) or {s["security_id"] for s in signals}!=set(forecasts):
            return no_trade("FORECAST_SIGNAL_UNIVERSE_MISMATCH")
        robust={s:finite(v,"prediction")-radius for s,v in forecasts.items()}
        if max(robust.values())<=0:
            return no_trade("NO_POSITIVE_UNCERTAINTY_ADJUSTED_ALPHA",monitor=latest)
        adapted=deepcopy(payload)
        # Preserve all independent constraints; only tighten gross exposure.
        constraints=adapted.setdefault("constraints",{})
        cap=finite(constraints.get("max_gross_exposure",1.),"max_gross_exposure",minimum=0)
        feedback=matured_feedback(registry_path,run_id,payload["account"]["account_id"],as_of)
        recent=feedback[-5:]
        if len(recent)==5 and all(r["net_return"]<0 for r in recent):
            return no_trade("MATURED_PAPER_LOSS_LIMIT",monitor=latest)
        feedback_scale=.5 if len(recent)>=3 and sum(r["net_return"] for r in recent)<0 else 1.
        confidence=(1-radius/width)*feedback_scale
        constraints["max_gross_exposure"]=min(cap,1.)*confidence
        constraints["method"]="mean_variance"
        constraints["turnover_cost_bps"]=max(finite(constraints.get("turnover_cost_bps",0.),"turnover_cost_bps",minimum=0),config["evaluation"]["cost_bps"])
        constraints["rebalance_sessions"]=1
        rank={s:i+1 for i,s in enumerate(sorted(robust,key=lambda s:(-robust[s],s)))}
        for s in adapted["signals"]:
            sid=s["security_id"]
            s["model_rank"]=rank[sid]
            s["factor_score"]=robust[sid]
            s["model_version"]=run_id
            # Forecasts are synthetic close-observed predictions; a caller
            # must provide independently timestamped, eligible synthetic signals.
        adapted["market"]["expected_returns"]={s["symbol"]:252*robust[s["security_id"]]
                                                for s in adapted["signals"]}
        maturity={r["label_end_date"] for r in clean[model] if r["date"]==day}
        if len(maturity)!=1:
            return no_trade("INCONSISTENT_PAPER_MATURITY")
        result=run_unified_portfolio(adapted)
        if result["decision"]=="BLOCK":
            return no_trade("INDEPENDENT_PORTFOLIO_GATE_BLOCKED",portfolio=result,monitor=latest)
        output=dict(decision=result["decision"],portfolio=result,monitor=latest,
                    risk_scale=confidence,matured_feedback_count=len(feedback),
                    feedback_scale=feedback_scale,selected_model=model,research_run_id=run_id,
                    forecast_provenance="caller_supplied_synthetic_streams",
                    **FLAGS)
        if result["decision"]=="SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE":
            paper_config=dict(as_of=as_of.isoformat(),maturity_date=next(iter(maturity)),
                research_run_id=run_id,account_id=payload["account"]["account_id"],
                annual_margin_rate=finite(constraints.get("annual_margin_rate",.065),"annual_margin_rate",minimum=0))
            decision_report={**output,"dataset_sha256":report["dataset_sha256"],
                             "status":"SYNTHETIC_PAPER_DECISION"}
            output["paper_decision_id"]=register(local_path(registry_path),paper_config,
                                                 decision_report,kind="experiment")
        return output
    except (ValueError,TypeError,KeyError,ArithmeticError) as exc:
        return no_trade(str(exc))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file",required=True,type=Path)
    p.add_argument("--feedback",action="store_true")
    a=p.parse_args()
    inputs=json.loads(a.file.read_text(encoding="utf-8"))
    result=record_paper_feedback(**inputs) if a.feedback else paper_decision(**inputs)
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=="__main__":
    main()
