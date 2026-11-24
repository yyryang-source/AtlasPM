"""Synthetic chronological parameter search; never approves real training."""
from __future__ import annotations
import argparse
import itertools
import json
from copy import deepcopy
from pathlib import Path
from math import prod
from atlaspm.engine.replay import replay
from atlaspm.engine.replay_performance import metrics

FIELDS = {"top_k", "rebalance_sessions", "cash_reserve", "optimizer.method",
          "optimizer.lookback_sessions", "optimizer.max_name_weight"}


def _apply(payload, params):
    out = deepcopy(payload)
    for key, value in params.items():
        if key.startswith("optimizer."):
            if not isinstance(out.get("optimizer"),dict):
                raise ValueError("OPTIMIZER_GRID_REQUIRES_BASE_CONFIG")
            out["optimizer"][key.split(".")[1]] = value
        else:
            out[key] = value
    return out


def _evaluate(run, start, end):
    curve = run["curve"]
    rows = curve[start:end]
    if len(rows) < 2:
        raise ValueError("INSUFFICIENT_EVALUATION_SESSIONS")
    returns = [r["daily_return"] for r in rows]
    stats = metrics(returns, [0.] * len(returns))
    return {k: stats[k] for k in ("total_return","max_drawdown","sharpe_zero_rf","annual_volatility")}


def search(payload, grid, validation_start, holdout_start):
    if payload.get("mode") != "synthetic" or payload.get("synthetic_fixture") is not True:
        raise ValueError("REAL_HISTORY_NOT_APPROVED")
    if not isinstance(grid,dict) or not grid or set(grid)-FIELDS:
        raise ValueError("INVALID_SEARCH_GRID")
    if any(not isinstance(v,list) or not v for v in grid.values()):
        raise ValueError("INVALID_GRID_VALUES")
    count = prod(len(v) for v in grid.values())
    if count > 128:
        raise ValueError("SEARCH_TRIAL_LIMIT")
    sessions = payload["sessions"]
    if type(validation_start) is not int or type(holdout_start) is not int:
        raise ValueError("INVALID_SPLIT")
    if not 2 <= validation_start < holdout_start-1 or holdout_start > len(sessions)-2:
        raise ValueError("INVALID_SPLIT")
    prefix = deepcopy(payload)
    prefix["sessions"] = sessions[:holdout_start]
    keys = sorted(grid)
    trials = []
    for values in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, values))
        try:
            run = replay(_apply(prefix,params))
            if not any(t["date"] < sessions[validation_start]["date"] for t in run["trades"]):
                raise ValueError("WARMUP_OR_NO_TRADES_BEFORE_VALIDATION")
            result = _evaluate(run,validation_start,holdout_start)
            score = result["sharpe_zero_rf"]
            if score is None:
                raise ValueError("UNDEFINED_VALIDATION_SHARPE")
            trials.append(dict(parameters=params,validation=result,score=score,status="OK"))
        except ValueError as exc:
            trials.append(dict(parameters=params,status="REJECTED",reason=str(exc)))
    valid = [t for t in trials if t["status"]=="OK"]
    if not valid:
        return dict(status="NO_VALID_TRIAL",trials=trials,research_only=True,
                    approved_for_model_training=False,live_execution_enabled=False,
                    broker_order_submission_enabled=False)
    winner = max(valid,key=lambda t:(t["score"],t["validation"]["total_return"]))
    # Only the selected configuration sees the holdout. No fallback on failure.
    run = replay(_apply(payload,winner["parameters"]))
    holdout = _evaluate(run,holdout_start,len(sessions))
    return dict(status="COMPLETE",trials=trials,selected_parameters=winner["parameters"],
                validation=winner["validation"],holdout=holdout,
                validation_dates=[sessions[validation_start]["date"],sessions[holdout_start-1]["date"]],
                holdout_dates=[sessions[holdout_start]["date"],sessions[-1]["date"]],
                selection_rule="validation_sharpe_zero_rf_then_return",
                carry_forward_positions=True,research_only=True,
                approved_for_model_training=False,live_execution_enabled=False,
                broker_order_submission_enabled=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",required=True,type=Path)
    args=parser.parse_args()
    config=json.loads(args.file.read_text(encoding="utf-8"))
    print(json.dumps(search(**config),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
