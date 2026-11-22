"""Independent SPY buy-and-hold benchmark aligned to stored backtest sessions."""

from __future__ import annotations

from math import sqrt
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from atlaspm.audit import _load, audit_backtests
from atlaspm.historical import TRADING_DAYS_PER_YEAR, load_adjusted_prices


def compare_spy(cash_path: Path, margin_path: Path, spy_path: Path) -> dict[str, Any]:
    """Compare two strategy curves with an independent SPY price CSV.

    SPY is not added to the portfolio optimizer's investment universe.
    All comparison periods are exact-matched, with no forward-filling.
    """
    audit = audit_backtests(cash_path, margin_path)
    cash = _load(cash_path)
    curves = cash["equity_curve"]
    executions = cash.get("rebalances", [])
    if not executions or not isinstance(executions[0].get("execution_date"), str):
        raise ValueError("Missing first execution date in cash backtest")
    anchor_date = executions[0]["execution_date"]
    from datetime import date
    anchor = date.fromisoformat(anchor_date)
    raw_prices = load_adjusted_prices(spy_path)
    if set(raw_prices) != {"SPY"}:
        raise ValueError("Benchmark file must contain SPY only")
    prices = raw_prices["SPY"]
    if anchor not in prices:
        raise ValueError(f"SPY price not found for initial execution date {anchor}")
    missing = [p["date"] for p in curves if date.fromisoformat(p["date"]) not in prices]
    if missing:
        raise ValueError(
            f"SPY missing {len(missing)} exact backtest dates; first: {missing[0]}"
        )
    prior_price = prices[anchor]
    equity = 1.0
    peak = 1.0
    spy_returns: list[float] = []
    spy_curve: list[dict[str, float | str]] = []
    for point in curves:
        day = point["date"]
        price = prices[date.fromisoformat(day)]
        one_day = price / prior_price - 1.0
        prior_price = price
        spy_returns.append(one_day)
        equity *= 1 + one_day
        peak = max(peak, equity)
        spy_curve.append({
            "date": day, "equity_index": equity, "drawdown": equity / peak - 1.0
        })
    count = len(spy_returns)
    vol = stdev(spy_returns) * sqrt(TRADING_DAYS_PER_YEAR) if count > 1 else 0.0
    sharpe = (fmean(spy_returns) * TRADING_DAYS_PER_YEAR / vol) if vol > 0 else None
    spy_cagr = equity ** (TRADING_DAYS_PER_YEAR / count) - 1.0
    spy_info = {
        "type": "SPY_adjusted_close_buy_and_hold_no_fees",
        "start_execution_date": anchor_date,
        "first_held_return_date": curves[0]["date"],
        "last_held_return_date": curves[-1]["date"],
        "annualized_compounded_return": spy_cagr,
        "annualized_volatility": vol,
        "max_drawdown": min(p["drawdown"] for p in spy_curve),
        "sharpe_zero_risk_free_assumption": sharpe,
        "ending_equity_index": equity,
    }
    result = {
        "method": "exact_date_independent_spy_buy_and_hold_comparison",
        "strategy_universe": audit["comparison_universe"],
        "backtest_dates": audit["date_range"],
        "backtest_observations": count,
        "spy": spy_info,
        "cash": {
            "annualized_compounded_return": cash["strategy"]["annualized_compounded_return"],
            "cagr_excess_vs_spy": cash["strategy"]["annualized_compounded_return"] - spy_cagr,
        },
        "margin": {
            "annualized_compounded_return": audit["margin"]["annualized_compounded_return"],
            "cagr_excess_vs_spy": audit["margin"]["annualized_compounded_return"] - spy_cagr,
        },
        "spy_curve": spy_curve,
        "warnings": [
            "SPY is an independent market ETF reference, not a risk-matched portfolio.",
            "SPY uses vendor adjusted close, idealized buy-and-hold and no fees, taxes or slippage.",
            "Portfolio stocks were selected with hindsight; survivorship and tuning bias remain.",
            "No broker margin liquidation is reflected in these historical strategy files.",
        ],
    }
    return result
