"""Aligned descriptive replay performance; synthetic price-only benchmarks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from math import sqrt, prod
from statistics import fmean, stdev
from copy import deepcopy

from atlaspm.engine.contracts import finite
from atlaspm.engine.replay import replay
from atlaspm.engine.allocation import METHODS


def metrics(returns, benchmark):
    if not returns or len(returns) != len(benchmark):
        raise ValueError("UNALIGNED_PERFORMANCE_SERIES")
    n = len(returns)
    growth = prod(1+r for r in returns)
    vol = stdev(returns)*sqrt(252) if n > 1 else 0.
    active = [r-b for r,b in zip(returns,benchmark)]
    tracking = stdev(active)*sqrt(252) if n > 1 else 0.
    mean, bm = fmean(returns), fmean(benchmark)
    variance = sum((b-bm)**2 for b in benchmark)
    beta = sum((r-mean)*(b-bm) for r,b in zip(returns,benchmark))/variance if variance else None
    wealth = peak = 1.
    drawdown = 0.
    for r in returns:
        wealth *= 1+r
        peak = max(wealth,peak)
        drawdown = min(drawdown,wealth/peak-1)
    downside = sqrt(fmean(min(0.,r)**2 for r in returns)*252)
    cagr = growth**(252/n)-1
    return dict(observations=n,total_return=growth-1,cagr=cagr,
                annual_volatility=vol,sharpe_zero_rf=mean*252/vol if vol else None,
                sortino_zero_target=mean*252/downside if downside else None,
                max_drawdown=drawdown,calmar=cagr/abs(drawdown) if drawdown else None,
                beta=beta,alpha_annual_arithmetic=(mean-beta*bm)*252 if beta is not None else None,
                tracking_error=tracking,information_ratio=fmean(active)*252/tracking if tracking else None,
                benchmark_total_return=prod(1+b for b in benchmark)-1)


def compare(payload, methods=None):
    if payload.get("mode") != "synthetic" or payload.get("synthetic_fixture") is not True:
        raise ValueError("REAL_HISTORY_NOT_APPROVED")
    methods = sorted(METHODS) if methods is None else list(methods)
    if not methods or len(set(methods)) != len(methods) or not set(methods)<=METHODS:
        raise ValueError("INVALID_COMPARISON_METHODS")
    if not isinstance(payload.get("optimizer"),dict):
        raise ValueError("COMPARISON_REQUIRES_COMMON_OPTIMIZER_CONFIG")
    runs = {}
    for method in methods:
        config = deepcopy(payload)
        config["optimizer"]["method"] = method
        runs[method] = replay(config)
    # One common evaluation interval after the first signal for every method.
    first_signals = {r["allocations"][0]["signal_date"] for r in runs.values() if r["allocations"]}
    if any(not r["allocations"] for r in runs.values()) or len(first_signals)!=1:
        raise ValueError("NO_COMMON_WARMUP_END")
    sessions = payload["sessions"]
    start = next(i for i,s in enumerate(sessions) if s["date"]==next(iter(first_signals)))
    if len(sessions)-start < 3:
        raise ValueError("REQUIRES_TWO_POST_WARMUP_RETURNS")
    # Benchmark prices must be supplied for each aligned session. Equal-weight
    # is a fixed initial basket, buy-and-hold, not a daily rebalanced strategy.
    basket = payload.get("benchmark_basket")
    if not isinstance(basket,list) or not basket or len(set(basket))!=len(basket):
        raise ValueError("EXPLICIT_UNIQUE_BENCHMARK_BASKET_REQUIRED")
    prices = []
    for row in sessions[start:]:
        marks = row.get("benchmark_close")
        if not isinstance(marks,dict) or set(marks)!={"SPY","QQQ"} or not set(basket)<=set(row["close"]):
            raise ValueError("BENCHMARK_MARK_COVERAGE_MISMATCH")
        prices.append({**{k:finite(v,k,minimum=.000001) for k,v in marks.items()},
                       "basket": {s:finite(row["close"][s],s,minimum=.000001) for s in basket}})
    levels = {"SPY":[p["SPY"]/prices[0]["SPY"] for p in prices],
              "QQQ":[p["QQQ"]/prices[0]["QQQ"] for p in prices],
              "equal_weight":[fmean(p["basket"][s]/prices[0]["basket"][s] for s in basket) for p in prices]}
    daily = {k:[b/a-1 for a,b in zip(v,v[1:])] for k,v in levels.items()}
    results = {}
    curves = {}
    for method,run in runs.items():
        curve=run["curve"][start:]
        if [p["date"] for p in curve]!=[s["date"] for s in sessions[start:]]:
            raise ValueError("REPLAY_DATE_ALIGNMENT_FAILED")
        returns=[p["daily_return"] for p in curve[1:]]
        trades=[t for t in run["trades"] if t["date"]>curve[0]["date"]]
        turnover=sum(t["shares"]*t["reference_price"] for t in trades)/curve[0]["equity"]
        results[method]={**metrics(returns,daily["SPY"]),
                         "gross_notional_over_start_equity":turnover,
                         "trade_cost":sum(t["cost"] for t in trades),"trade_count":len(trades)}
        curves[method]=[p["equity"]/curve[0]["equity"] for p in curve]
    return dict(research_only=True,approved_for_model_training=False,live_execution_enabled=False,
                broker_order_submission_enabled=False,historical_security_identity_verified=False,
                classification="SYNTHETIC_PRICE_ONLY_BENCHMARKS",
                dates=[s["date"] for s in sessions[start:]],strategies=results,
                benchmarks={k:metrics(v,daily["SPY"]) for k,v in daily.items()},
                strategy_equity_indices=curves,benchmark_equity_indices=levels,
                benchmark_assumption="frictionless fractional buy-and-hold from signal close; no dividends")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",required=True,type=Path)
    parser.add_argument("--methods",nargs="+",choices=sorted(METHODS))
    args=parser.parse_args()
    print(json.dumps(compare(json.loads(args.file.read_text(encoding="utf-8")),args.methods),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
