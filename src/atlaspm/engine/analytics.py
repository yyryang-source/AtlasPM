"""Unified reproducible *descriptive* portfolio analytics, never attainable P&L."""
from __future__ import annotations

from math import sqrt
from statistics import fmean, stdev
from typing import Any, Mapping

from atlaspm.engine.contracts import finite
from atlaspm.monitoring.performance import _active_stats, _compound

TRADING_SESSIONS = 252


def _stats(returns: list[float], benchmark: list[float]) -> dict[str, Any]:
    n = len(returns)
    annual = TRADING_SESSIONS
    growth = 1 + _compound(returns)
    if growth <= 0:
        raise ValueError("BANKRUPT_SYNTHETIC_EQUITY_CURVE")
    mean = fmean(returns)
    vol = stdev(returns) * sqrt(annual)
    downside = sqrt(sum(min(0., r)**2 for r in returns) / n) * sqrt(annual)
    wealth, peak = 1., 1.
    worst, active, longest = 0., 0, 0
    for r in returns:
        wealth *= 1+r
        peak = max(peak, wealth)
        dd = wealth/peak-1.
        worst = min(worst, dd)
        active = active+1 if dd < -1e-12 else 0
        longest = max(longest, active)
    cagr = growth**(annual/n)-1
    active_stats = _active_stats(returns, benchmark)
    beta = active_stats["beta_to_spy"]
    alpha = (mean - beta*fmean(benchmark))*annual if beta is not None else None
    return {
        "total_compounded_return": growth-1,
        "cagr": cagr, "annual_volatility": vol,
        "sharpe_zero_risk_free": mean * annual / vol if vol > 0 else None,
        "sortino_zero_target": mean * annual / downside if downside > 0 else None,
        "calmar": cagr / abs(worst) if worst < 0 else None,
        "max_drawdown": worst, "max_drawdown_duration_sessions": longest,
        "beta": beta, "alpha_arithmetic_annual_zero_risk_free": alpha,
        "active_vs_benchmark": active_stats,
    }


def build_analytics(history: object, *, gross_notional: float, equity: float,
                    order_fees: float, slippage_cost: float, market_impact_cost: float,
                    annual_interest_assumption: float, margin_debt: float,
                    factor_scores: Mapping[str, float],
                    variance_contributions: Mapping[str, float]) -> dict[str, Any]:
    if not isinstance(history, dict):
        history = {}
    annual_rate = finite(annual_interest_assumption, "annual_interest_rate", minimum=0)
    if gross_notional < 0 or equity <= 0:
        raise ValueError("INVALID_ANALYTICS_DENOMINATOR")
    series: dict[str, Any] = {}
    for key in ("portfolio", "spy", "qqq", "equal_weight"):
        raw = history.get(key)
        if raw is None:
            continue
        if not isinstance(raw, list):
            raise ValueError(f"history.{key} must be a list")
        series[key] = [finite(v, f"history.{key}") for v in raw]
        if any(v <= -1 for v in series[key]):
            raise ValueError("INVALID_HISTORICAL_RETURN_BELOW_MINUS_100PCT")
    performance: dict[str, Any] | None = None
    if series:
        if set(series) != {"portfolio", "spy", "qqq", "equal_weight"}:
            raise ValueError("INCOMPLETE_ALIGNED_BENCHMARK_SERIES")
        if min(map(len, series.values())) < 3 or len(set(map(len, series.values()))) != 1:
            raise ValueError("HISTORY_REQUIRES_ALIGNED_SERIES_LENGTH_3_OR_MORE")
        performance = {
            "portfolio": _stats(series["portfolio"], series["spy"]),
            "benchmarks": {
                "SPY": _stats(series["spy"], series["spy"]),
                "QQQ": _stats(series["qqq"], series["spy"]),
                "equal_weight": _stats(series["equal_weight"], series["spy"]),
            },
            "observation_count": len(series["portfolio"]),
        }
    annual_interest = margin_debt * annual_rate
    return {
        "classification": "SYNTHETIC_RESEARCH_ONLY_NOT_ACTUAL_OR_ACHIEVABLE_RETURNS",
        "performance": performance,
        "unavailable_reason": None if performance else "NO_ALIGNED_RESEARCH_RETURN_HISTORY",
        "gross_trade_notional": gross_notional,
        "one_way_turnover_fraction": gross_notional / equity,
        "paper_fees_usd": order_fees, "paper_slippage_usd": slippage_cost,
        "paper_market_impact_assumption_usd": market_impact_cost,
        "annual_margin_interest_estimate_usd": annual_interest,
        "annual_interest_drag_fraction_of_equity": annual_interest/equity,
        "factor_score_exposure_illustrative": dict(factor_scores),
        "variance_contributions_illustrative": dict(variance_contributions),
        "research_only": True, "live_execution_enabled": False,
    }
