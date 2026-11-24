"""Comparable, one-session delayed synthetic strategy research metrics."""
from __future__ import annotations

from itertools import groupby
from math import ceil, sqrt, exp, log
from random import Random
from statistics import fmean, stdev

from atlaspm.research.training_engine import _correlation, _ranks


def ratio(values):
    sd = stdev(values) if len(values) > 1 else 0.
    return fmean(values) / sd if sd else None


def evaluate(rows, dates, *, cost_bps, fraction=.25, regimes=None):
    """Use previous close's ranking for next close-to-close return.
    
    Missing paths invalidate the entire evaluation day, not just losers.
    A discontinuity is charged as a fresh entry; incomplete coverage is
    explicitly reported and never eligible for synthetic admission.
    """
    days = {day: {r["security_id"]: r for r in group}
            for day, group in groupby(rows, key=lambda r: r["date"])}
    equity = peak = 1.
    drawdown = 0.
    previous_weights = {}
    net, benchmarks, turnovers, costs, ics = [], [], [], [], []
    by_regime = {}
    missing = 0
    for old, day in zip(dates, dates[1:]):
        scores, current = days.get(old, {}), days.get(day, {})
        if (not scores or scores.keys() != current.keys() or
                any(r["actual"] is None for r in current.values())):
            missing += 1
            previous_weights = {}
            continue
        ids = sorted(scores)
        values = [scores[s]["prediction"] for s in ids]
        actual = [current[s]["actual"] for s in ids]
        cutoff = sorted(values, reverse=True)[max(0, ceil(len(ids)*fraction)-1)]
        chosen = [s for s in ids if scores[s]["prediction"] >= cutoff]
        target = {s: 1/len(chosen) for s in chosen}
        turnover = sum(abs(target.get(s, 0.)-previous_weights.get(s, 0.))
                       for s in target.keys() | previous_weights.keys())
        gross = fmean(current[s]["actual"] for s in chosen)
        cost = turnover * cost_bps / 10000
        result = (1-cost)*(1+gross)-1
        if result <= -1:
            raise ValueError("SYNTHETIC_RETURN_BANKRUPTCY")
        previous_weights = {s: w*(1+current[s]["actual"])/(1+gross)
                            for s,w in target.items()}
        equity *= 1+result
        peak = max(peak, equity)
        drawdown = max(drawdown, 1-equity/peak)
        net.append(result)
        benchmarks.append(fmean(actual))
        turnovers.append(turnover)
        costs.append(cost)
        ic = _correlation(_ranks(values), _ranks(actual))
        if ic is not None:
            ics.append(ic)
        if regimes is not None:
            by_regime.setdefault(regimes[old], []).append(result-benchmarks[-1])
    n = len(net)
    excess = [a-b for a,b in zip(net,benchmarks)]
    ir = ratio(excess)
    sharpe = ratio(net)
    downside = sqrt(fmean(min(0., x)**2 for x in net)) if n else 0.
    # Annualized statistics assume 252 equally spaced trading sessions.
    annual_log = log(equity)*252/n if n else 0.
    cagr = exp(annual_log)-1 if n and annual_log < 700 else None
    beta = None
    if n > 1 and stdev(benchmarks):
        beta = sum((a-fmean(net))*(b-fmean(benchmarks)) for a,b in zip(net,benchmarks)) / ((n-1)*stdev(benchmarks)**2)
    tail = sorted(net)[:max(1, ceil(n*.05))]
    return dict(sessions=n, missing_sessions=missing, cagr=cagr,
                sharpe=sharpe*sqrt(252) if sharpe is not None else None,
                sortino=fmean(net)*sqrt(252)/downside if downside else None,
                calmar=cagr/drawdown if drawdown and cagr is not None else None,
                max_drawdown=drawdown, rank_ic=fmean(ics) if ics else None,
                icir=ratio(ics), information_ratio=ir*sqrt(252) if ir is not None else None,
                alpha_annualized=252*(fmean(net)-beta*fmean(benchmarks)) if beta is not None else None,
                beta=beta, cvar_95=-fmean(tail) if tail else None,
                turnover=fmean(turnovers) if n else None,
                transaction_cost_sum=sum(costs), mean_excess=fmean(excess) if n else None,
                regime_excess={k:dict(sessions=len(v),mean_excess=fmean(v)) for k,v in sorted(by_regime.items())},
                excess=excess)


def block_lower(values, *, trials, seed=42, draws=500, block=5):
    """One-sided moving circular-block bootstrap with Bonferroni correction.
    Descriptive small-sample diagnostic, not a guarantee of significance.
    """
    if len(values) < 2*block:
        return None
    rng = Random(seed)
    n = len(values)
    means = []
    for _ in range(draws):
        sample = []
        while len(sample) < n:
            i = rng.randrange(n)
            sample.extend(values[(i+j)%n] for j in range(block))
        means.append(fmean(sample[:n]))
    return sorted(means)[max(0, int(draws*.05/trials)-1)]


def summary(rows, dates, *, cost_bps, trials, regimes=None):
    result = evaluate(rows, dates, cost_bps=cost_bps, regimes=regimes)
    result["bootstrap_lower_excess"] = block_lower(result.pop("excess"), trials=trials)
    stressed = evaluate(rows, dates, cost_bps=2*cost_bps, regimes=regimes)
    result["double_cost_mean_excess"] = stressed["mean_excess"]
    return result


def eligible(metrics, *, minimum_sessions, max_drawdown):
    return (metrics["sessions"] >= minimum_sessions and not metrics["missing_sessions"]
            and metrics["max_drawdown"] <= max_drawdown
            and metrics["bootstrap_lower_excess"] is not None
            and metrics["bootstrap_lower_excess"] > 0
            and metrics["double_cost_mean_excess"] is not None
            and metrics["double_cost_mean_excess"] > 0)


def selection_score(metrics):
    return ((metrics["information_ratio"] or 0.) + (metrics["rank_ic"] or 0.)
            - 2*metrics["max_drawdown"] - (metrics["turnover"] or 0.))
