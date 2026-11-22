"""Risk-adjusted historical comparisons to an exact-date SPY benchmark.

These descriptive statistics do not establish causal alpha or predict future
performance. All risk-free-rate assumptions are explicitly zero.
"""

from __future__ import annotations

from datetime import date
from math import isfinite, sqrt
from statistics import fmean, stdev
from typing import Any

from atlaspm.historical import TRADING_DAYS_PER_YEAR


def _daily_returns(values: list[float]) -> list[float]:
    previous = 1.0
    returns: list[float] = []
    for value in values:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("Non-numeric equity index")
        if not isfinite(value) or value <= 0:
            raise ValueError("Equity index must remain positive and finite")
        returns.append(value / previous - 1.0)
        previous = value
    return returns


def _compound(returns: list[float]) -> float:
    wealth = 1.0
    for r in returns:
        wealth *= 1 + r
    return wealth - 1.0


def _active_stats(
    strategy: list[float],
    spy: list[float],
) -> dict[str, float | int | None]:
    if len(strategy) != len(spy) or len(strategy) < 2:
        raise ValueError("Active risk calculation needs two aligned return series")
    n = len(spy)
    mu_s, mu_b = fmean(strategy), fmean(spy)
    diffs = [s - b for s, b in zip(strategy, spy)]
    tracking_error = stdev(diffs) * sqrt(TRADING_DAYS_PER_YEAR)
    spy_variance_sum = sum((b - mu_b) ** 2 for b in spy)
    covariance_sum = sum((s - mu_s) * (b - mu_b) for s, b in zip(strategy, spy))
    strat_variance_sum = sum((s - mu_s) ** 2 for s in strategy)
    beta = covariance_sum / spy_variance_sum if spy_variance_sum > 0 else None
    correlation = (
        covariance_sum / sqrt(spy_variance_sum * strat_variance_sum)
        if spy_variance_sum > 0 and strat_variance_sum > 0 else None
    )
    active_cagr = (
        (1 + _compound(strategy)) ** (TRADING_DAYS_PER_YEAR / n)
        - (1 + _compound(spy)) ** (TRADING_DAYS_PER_YEAR / n)
    )
    return {
        "daily_observations": n,
        "beta_to_spy": beta,
        "daily_correlation_to_spy": correlation,
        "annual_tracking_error": tracking_error,
        "information_ratio_zero_risk_free_assumption": (
            fmean(diffs) * TRADING_DAYS_PER_YEAR / tracking_error
            if tracking_error > 0 else None
        ),
        "annualized_arithmetic_active_return": fmean(diffs) * TRADING_DAYS_PER_YEAR,
        "cagr_difference_vs_spy": active_cagr,
    }


def historical_spy_diagnostics(
    dates: list[str], cash_index: list[float],
    margin_index: list[float], spy_index: list[float]
) -> dict[str, Any]:
    """Compare strategy and SPY daily equity paths and each calendar year.

    Initial index is 1.0 for every series before the first provided day.
    Caller must enforce identical session dates for all three inputs.
    """
    n = len(dates)
    if n < 2 or len(cash_index) != n or len(margin_index) != n or len(spy_index) != n:
        raise ValueError("Expected equally sized daily equity series of length >= 2")
    parsed = [date.fromisoformat(day) for day in dates]
    if parsed != sorted(set(parsed)):
        raise ValueError("Dates must be strictly increasing")
    returns = {
        "cash": _daily_returns(cash_index),
        "margin": _daily_returns(margin_index),
        "spy": _daily_returns(spy_index),
    }
    full = {
        "cash": _active_stats(returns["cash"], returns["spy"]),
        "margin": _active_stats(returns["margin"], returns["spy"]),
    }
    yearly: list[dict[str, Any]] = []
    for year in sorted(set(day.year for day in parsed)):
        indices = [i for i, day in enumerate(parsed) if day.year == year]
        values = {
            name: _compound([rows[i] for i in indices])
            for name, rows in returns.items()
        }
        yearly.append({
            "year": year,
            "first_date": dates[indices[0]],
            "last_date": dates[indices[-1]],
            "trading_days": len(indices),
            "partial_year": year in {parsed[0].year, parsed[-1].year},
            "cash_return": values["cash"],
            "margin_return": values["margin"],
            "spy_return": values["spy"],
            "cash_excess_return_vs_spy": values["cash"] - values["spy"],
            "margin_excess_return_vs_spy": values["margin"] - values["spy"],
        })
    complete = [r for r in yearly if not r["partial_year"]]
    return {
        "method": "daily_active_return_and_yearly_compounded_return",
        "full_period": full,
        "calendar_years": yearly,
        "complete_years_count": len(complete),
        "cash_outperforming_complete_years": sum(
            r["cash_excess_return_vs_spy"] > 0 for r in complete
        ),
        "margin_outperforming_complete_years": sum(
            r["margin_excess_return_vs_spy"] > 0 for r in complete
        ),
        "warnings": [
            "Beta, tracking error and information ratio are historical in-sample statistics, not verified alpha.",
            "Annual excess is strategy calendar-year return minus SPY return (percentage points); partial years are flagged.",
            "Information ratio is annualized mean daily active return divided by tracking error, NOT a confidence-adjusted forecast.",
            "The first and last calendar years are flagged partial, even if their observed dates span most of a year.",
            "The fixed strategy universe was selected with hindsight; survivorship and selection bias remain.",
        ],
    }
