"""Synthetic forecast-to-portfolio closed loop with three chronological stages.

Model selection, strategy selection and final cash replay are separated.
Reuse the existing trainer, adaptive ensemble, portfolio optimizer and
cash execution simulator. Never grant broker or real-data permissions.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from math import isfinite
from statistics import fmean
import json
from pathlib import Path

from atlaspm.engine.contracts import finite, timestamp
from atlaspm.engine.replay import replay
from atlaspm.engine.replay_performance import metrics
from atlaspm.engine.allocation import METHODS
from atlaspm.research.training_dataset import validate_panel_metadata
from atlaspm.research.training_tournament import tournament, project_features, _sha
from atlaspm.research.training_engine import train
from atlaspm.research.training_registry import FLAGS

STRATEGY_FIELDS={"top_k","rebalance_sessions","cash_reserve","optimizer"}
EXECUTION_FIELDS={"initial_cash","fee_bps","slippage_bps","settlement_sessions","execution_policy"}


def _validate_market(payload,market):
    if not isinstance(market,dict) or market.get("mode")!="synthetic" or market.get("synthetic_fixture") is not True:
        raise ValueError("SYNTHETIC_EXECUTION_FIXTURE_REQUIRED")
    if set(market)!={"mode","synthetic_fixture","sessions","sectors"}:
        raise ValueError("INVALID_EXECUTION_MARKET_FIELDS")
    sessions=payload["sessions"]
    prices=market["sessions"]
    sectors=market["sectors"]
    if not isinstance(prices,list) or len(prices)!=len(sessions) or not isinstance(sectors,dict):
        raise ValueError("MARKET_PANEL_ALIGNMENT")
    if not 1<=len(sectors)<=20 or any(not isinstance(s,str) or not s or not isinstance(v,str) or not v for s,v in sectors.items()):
        raise ValueError("SYNTHETIC_UNIVERSE_REQUIRES_1_TO_20_NAMES")
    for sample,row in zip(sessions,prices):
        if set(row)-{"date","open_at","close_at","source","open","close","volume"}:
            raise ValueError("UNEXPECTED_MARKET_FIELD")
        if row["date"]!=sample["date"] or row["close_at"]!=sample["close_at"] or row.get("source")!="synthetic":
            raise ValueError("MARKET_PANEL_ALIGNMENT")
        opening=timestamp(row["open_at"],"open_at")
        closing=timestamp(row["close_at"],"close_at")
        if not opening<closing or opening.date().isoformat()!=row["date"] or closing.date().isoformat()!=row["date"]:
            raise ValueError("INVALID_EXECUTION_SESSION")
        marks={a["security_id"][4:]:a for a in sample["assets"] if a["security_id"].startswith("SYN:") and a.get("source")=="synthetic"}
        if len(marks)!=len(sample["assets"]) or set(marks)!=set(sectors) or set(row["open"])!=set(sectors) or set(row["close"])!=set(sectors):
            raise ValueError("CLOSED_LOOP_REQUIRES_FIXED_SYNTHETIC_UNIVERSE")
        for s in sectors:
            finite(row["open"][s],"open",minimum=.000001)
            close=finite(row["close"][s],"close",minimum=.000001)
            if close!=finite(marks[s]["close"],"panel_close",minimum=.000001):
                raise ValueError("TRAINING_AND_EXECUTION_CLOSE_MISMATCH")
    return sectors


def _strategy(raw):
    if not isinstance(raw,dict) or set(raw)-STRATEGY_FIELDS:
        raise ValueError("INVALID_STRATEGY_CONFIG")
    s={"top_k":2,"rebalance_sessions":5,"cash_reserve":.1,**raw}
    for k,maximum in (("top_k",20),("rebalance_sessions",63)):
        if type(s[k]) is not int or not 1<=s[k]<=maximum:
            raise ValueError("INVALID_STRATEGY_INTEGER:"+k)
    if not 0<=finite(s["cash_reserve"],"cash_reserve")<1:
        raise ValueError("INVALID_CASH_RESERVE")
    if "optimizer" in s:
        opt=s["optimizer"]
        if not isinstance(opt,dict) or opt.get("method","equal_weight") not in METHODS:
            raise ValueError("INVALID_STRATEGY_OPTIMIZER")
        if finite(opt.get("max_gross_exposure",1),"max_gross_exposure")>1:
            raise ValueError("CLOSED_LOOP_CASH_ONLY")
    return s


def _signals(rows,market,model,version):
    forecasts=defaultdict(dict)
    days={r["date"] for r in market["sessions"]}
    for r in rows:
        day=r["date"]
        sid=r["security_id"]
        if day not in days or not isinstance(sid,str) or not sid.startswith("SYN:"):
            raise ValueError("FORECAST_MARK_ALIGNMENT")
        symbol=sid[4:]
        if symbol not in market["sectors"] or symbol in forecasts[day]:
            raise ValueError("DUPLICATE_OR_UNKNOWN_FORECAST")
        forecasts[day][symbol]=finite(r["prediction"],"prediction")
    sessions=[]
    for r in market["sessions"]:
        day=r["date"]
        values=forecasts.get(day,{})
        if values and set(values)!=set(market["sectors"]):
            raise ValueError("INCOMPLETE_CROSS_SECTION_FORECAST")
        close=r["close_at"]
        ordered=sorted(values,key=lambda s:(-values[s],s))
        signals=[dict(symbol=s,security_id="SYN:"+s,sector=market["sectors"][s],
                      model_version=version+":"+model,identity_source="synthetic",
                      data_source="synthetic",data_quality="synthetic_test_only",
                      research_eligible=True,model_approved_for_training=False,
                      generated_at=close,available_at=close,data_available_at=close,
                      expires_at=close,factor_score=values[s],model_rank=i+1)
                 for i,s in enumerate(ordered)]
        sessions.append({**r,"signals":signals})
    return sessions


def _evaluate(sessions,strategy,execution,*,include_path=False):
    request={"mode":"synthetic","synthetic_fixture":True,**execution,**strategy,
             "missing_signal_policy":"hold","sessions":sessions}
    result=replay(request)
    curve=result["curve"]
    returns=[p["daily_return"] for p in curve]
    # Use cash (zero return) as explicit baseline; no invented market index.
    stats=metrics(returns,[0.]*len(returns))
    initial_marks=sessions[0]["close"]
    levels=[fmean(row["close"][s]/p for s,p in initial_marks.items()) for row in sessions]
    benchmark=[0.]+[b/a-1 for a,b in zip(levels,levels[1:])]
    benchmark_stats=metrics(returns,benchmark)
    trades=result["trades"]
    summary={"statistics":stats,"trade_count":len(trades),"total_cost":result["total_cost"],
             "gross_notional_over_initial_cash":sum(t["shares"]*t["reference_price"] for t in trades)/request["initial_cash"],
             "first_date":curve[0]["date"],"last_date":curve[-1]["date"],
             "equal_weight_price_benchmark":benchmark_stats,
             "forecast_sessions":sum(bool(r["signals"]) for r in sessions),
             "execution_model":result["execution_model"]}
    if include_path:
        summary.update(curve=curve,trades=trades,allocations=result["allocations"],
                       orders=result["orders"],unexecuted_final_signal=result["unexecuted_final_signal"])
    return summary


def run(payload,market,*,model_config,model_candidates,model_selection_end,
        strategy_holdout_start,strategies,execution,selection_metric="mae",
        signal_model="selected",ensemble=None,max_validation_drawdown=.25):
    if not isinstance(model_config,dict):
        raise ValueError("INVALID_MODEL_CONFIG")
    validate_panel_metadata(payload,model_config.get("horizon",5),model_config.get("sequence_length",1))
    _validate_market(payload,market)
    n=len(payload["sessions"])
    h=model_config.get("horizon",5)
    if (type(model_selection_end) is not int or type(strategy_holdout_start) is not int
            or not 0<model_selection_end<strategy_holdout_start-h-2
            or strategy_holdout_start>=n-h-2):
        raise ValueError("INVALID_THREE_STAGE_SPLIT")
    if not isinstance(execution,dict) or set(execution)-EXECUTION_FIELDS or "initial_cash" not in execution:
        raise ValueError("INVALID_EXECUTION_CONFIG")
    finite(execution["initial_cash"],"initial_cash",minimum=.01)
    if not isinstance(strategies,list) or not 2<=len(strategies)<=32:
        raise ValueError("REQUIRES_2_TO_32_STRATEGIES")
    configs=[_strategy(s) for s in strategies]
    if len({_sha(s) for s in configs})!=len(configs):
        raise ValueError("DUPLICATE_STRATEGY")
    cap=finite(max_validation_drawdown,"max_validation_drawdown")
    if not 0<cap<=1 or signal_model not in ("selected","adaptive_ensemble"):
        raise ValueError("INVALID_STRATEGY_SELECTION_POLICY")
    if ensemble is not None and not isinstance(ensemble,dict):
        raise ValueError("INVALID_ENSEMBLE_POLICY")
    # Stage 1: candidate selection sees only the prefix before the model
    # selection boundary. No strategy or final holdout results enter it.
    selection=tournament(payload,config=model_config,candidates=model_candidates,
        holdout_start=model_selection_end,selection_metric=selection_metric,selection_only=True)
    opts=dict(selection["selected_config"])
    winner=selection["selected_model"]
    selected_name=winner
    if signal_model=="adaptive_ensemble":
        if winner=="historical_mean":
            raise ValueError("ENSEMBLE_REQUIRES_NONBASELINE_WINNER")
        opts["models"]=["historical_mean",winner]
        opts["ensemble"]={"window_dates":20,"minimum_dates":5} if ensemble is None else ensemble
        selected_name="adaptive_ensemble"
    # Stage 2: fit only selected model/policy on data ending BEFORE the
    # final strategy holdout. Candidate strategy executions start in cash.
    prefix={**payload,"sessions":payload["sessions"][:strategy_holdout_start]}
    prefix_market={**market,"sessions":market["sessions"][:strategy_holdout_start]}
    fitted=train(project_features(prefix,selection["selected_features"]),**opts)
    version=selection["experiment_sha256"]
    rows=_signals(fitted["predictions"][selected_name],prefix_market,selected_name,version)
    validation=rows[model_selection_end:]
    trials=[]
    for index,s in enumerate(configs):
        try:
            result=_evaluate(validation,s,execution)
            stats=result["statistics"]
            score=stats["sharpe_zero_rf"]
            eligible=(score is not None and isfinite(score) and result["trade_count"]>0
                      and abs(stats["max_drawdown"])<=cap)
            trials.append({"index":index,"strategy":s,"validation":result,"eligible":eligible,
                           "status":"ELIGIBLE" if eligible else "VALIDATION_RISK_OR_ACTIVITY_REJECTED"})
        except ValueError as exc:
            trials.append({"index":index,"strategy":s,"eligible":False,"status":"REJECTED","reason":str(exc)})
    eligible=[t for t in trials if t["eligible"]]
    base={"schema":"atlaspm_synthetic_strategy_closed_loop_v7","selection":selection,
          "strategy_trials":trials,"strategy_selection_rule":"validation_net_sharpe_with_drawdown_cap",
          "max_validation_drawdown":cap,"model_selection_end":payload["sessions"][model_selection_end]["date"],
          "strategy_holdout_start":payload["sessions"][strategy_holdout_start]["date"],**FLAGS}
    if not eligible:
        return {**base,"status":"NO_VALID_STRATEGY","holdout_evaluated":False}
    best=max(eligible,key=lambda t:(t["validation"]["statistics"]["sharpe_zero_rf"],-t["index"]))
    # Stage 3: only selected strategy enters independent fresh-cash holdout.
    # No fallback or selection based on holdout performance.
    full=train(project_features(payload,selection["selected_features"]),**opts)
    final_rows=_signals(full["predictions"][selected_name],market,selected_name,version)
    final=_evaluate(final_rows[strategy_holdout_start:],best["strategy"],execution,include_path=True)
    return {**base,"status":"SYNTHETIC_CASH_CLOSED_LOOP_COMPLETE",
            "dataset_sha256":full["dataset_sha256"],"holdout_evaluated":True,
            "selected_strategy":best["strategy"],"selected_strategy_index":best["index"],
            "holdout":final,"fresh_cash_per_evaluation":True,
            "holdout_drawdown_within_validation_cap":abs(final["statistics"]["max_drawdown"])<=cap,
            "missing_forecast_policy":"hold_existing_positions_no_new_orders",
            "limitations":["synthetic fixed universe at most 20 assets","cash only; no borrowing",
                "no real identity, corporate actions, liquidity or broker verification",
                "terminal horizon forecasts are censored by existing trainer; hold existing positions",
                "risk cap is validation filter, not a guaranteed holdout drawdown limit"]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file",required=True,type=Path)
    a=p.parse_args()
    print(json.dumps(run(**json.loads(a.file.read_text(encoding="utf-8"))),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
