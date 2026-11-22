"""Leakage-safe rolling-window portfolio research backtest (not trading)."""

from __future__ import annotations

from math import isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from atlaspm.historical import TRADING_DAYS_PER_YEAR, load_adjusted_prices
from atlaspm.mean_variance import optimize_mean_variance


def _sample_estimates(window: list[list[float]], symbols: list[str]) -> tuple[dict[str, float], list[list[float]]]:
    """Annualized arithmetic means and unbiased sample covariance, past only."""
    n = len(window)
    size = len(symbols)
    means = [fmean(row[i] for row in window) for i in range(size)]
    cov = [
        [
            TRADING_DAYS_PER_YEAR * sum(
                (row[i] - means[i]) * (row[j] - means[j])
                for row in window
            ) / (n - 1)
            for j in range(size)
        ]
        for i in range(size)
    ]
    return ({s: means[i] * TRADING_DAYS_PER_YEAR for i, s in enumerate(symbols)}, cov)


def _metrics(daily_returns: list[float], curve: list[dict[str, Any]], final_value: float) -> dict[str, float | None]:
    n = len(daily_returns)
    vol = stdev(daily_returns) * sqrt(TRADING_DAYS_PER_YEAR) if n >= 2 else 0.0
    avg = fmean(daily_returns)
    # Return stats assume 252 sessions per year and zero risk-free rate.
    sharpe = avg * TRADING_DAYS_PER_YEAR / vol if vol > 0 else None
    return {
        "total_return": final_value - 1.0,
        "annualized_compounded_return": final_value ** (TRADING_DAYS_PER_YEAR / n) - 1.0,
        "annualized_volatility": vol,
        "sharpe_zero_risk_free_assumption": sharpe,
        "max_drawdown": min(point["drawdown"] for point in curve),
        "ending_equity_index": final_value,
    }


def _rebalance(
    existing: list[float], equity: float, weights: list[float],
    leverage: float, fee_rate: float,
) -> tuple[list[float], float, float, float]:
    """Target weights at the close, with each traded leg charged per notional."""
    fee = 0.0
    # Solve fee = rate * sum(abs(new holdings after fee - old holdings)).
    for _ in range(100):
        post_fee = equity - fee
        if post_fee <= 0:
            raise ValueError("Trading costs exhausted portfolio equity")
        holdings = [post_fee * leverage * w for w in weights]
        new_fee = fee_rate * sum(abs(h - old) for h, old in zip(holdings, existing))
        if abs(new_fee - fee) < 1e-14:
            fee = new_fee
            break
        fee = new_fee
    post_fee = equity - fee
    holdings = [post_fee * leverage * w for w in weights]
    debt = (leverage - 1) * post_fee
    turnover = sum(abs(h - old) for h, old in zip(holdings, existing)) / equity
    return holdings, debt, fee, turnover


def backtest_walk_forward(
    path: Path,
    *,
    lookback_days: int = 756,
    rebalance_days: int = 63,
    risk_aversion: float = 4.0,
    max_weight: float = 0.60,
    leverage: float = 1.0,
    annual_financing_rate: float = 0.065,
    fee_bps: float = 5.0,
    maintenance_fraction: float = 0.30,
) -> dict[str, Any]:
    """Use closes through t-1 for signals, trade at t close, hold t to t+1.

    Index begins at 1 equity unit, with no deposits/withdrawals, price data
    pre-aligned on common dates, zero cash yield and no dividends beyond adjusted
    prices. Underlying price data is NOT point-in-time verified.
    """
    for name, value, minimum in (
        ("lookback_days", lookback_days, 3),
        ("rebalance_days", rebalance_days, 1),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    for name, value, lo, hi in (
        ("risk_aversion", risk_aversion, 0, None),
        ("max_weight", max_weight, 0, 1),
        ("leverage", leverage, 1, 4),
        ("annual_financing_rate", annual_financing_rate, 0, None),
        ("fee_bps", fee_bps, 0, 1000),
        ("maintenance_fraction", maintenance_fraction, 0, 1),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"{name} must be finite")
        if value < lo or (name in {"risk_aversion", "max_weight", "maintenance_fraction"} and value == 0) or (hi is not None and value > hi):
            raise ValueError(f"{name} is outside the supported range")

    prices = load_adjusted_prices(path)
    symbols = sorted(prices)
    common_dates = sorted(set.intersection(*(set(prices[s]) for s in symbols)))
    if len(common_dates) <= lookback_days + 2:
        raise ValueError("Not enough shared dates for a training window and held-out returns")
    price_rows = [[prices[s][day] for s in symbols] for day in common_dates]
    daily_asset_returns = [
        [curr / prev - 1.0 for curr, prev in zip(price_rows[i + 1], price_rows[i])]
        for i in range(len(price_rows) - 1)
    ]
    size = len(symbols)
    equity = 1.0
    holdings = [0.0] * size
    debt = 0.0
    benchmark_holdings = [1.0 / size] * size
    benchmark = 1.0
    peak = 1.0
    benchmark_peak = 1.0
    curve: list[dict[str, Any]] = []
    benchmark_daily: list[float] = []
    strategy_daily: list[float] = []
    rebalances: list[dict[str, Any]] = []
    fees_paid = 0.0
    financing_paid = 0.0
    margin_breach_days = 0
    first_margin_breach_date = None
    rate_per_day = annual_financing_rate / TRADING_DAYS_PER_YEAR

    # Signal at t-1 close, execute t close, hold exposure until t+1 close.
    for t in range(lookback_days + 1, len(common_dates) - 1):
        close = common_dates[t].isoformat()
        next_close = common_dates[t + 1].isoformat()
        old_equity = equity
        if (t - lookback_days - 1) % rebalance_days == 0:
            window = daily_asset_returns[t - lookback_days - 1:t - 1]
            expected, covariance = _sample_estimates(window, symbols)
            result = optimize_mean_variance(
                expected, covariance, risk_aversion=risk_aversion,
                max_weight=max_weight, leverage=leverage,
                annual_financing_rate=annual_financing_rate,
            )
            new_weights = [result["weights"][s] for s in symbols]
            holdings, debt, cost, turnover = _rebalance(
                holdings, equity, new_weights, leverage, fee_bps / 10000.0
            )
            fees_paid += cost
            equity -= cost
            rebalances.append({
                "signal_date": common_dates[t - 1].isoformat(),
                "execution_date": close,
                "training_start_date": common_dates[t - lookback_days - 1].isoformat(),
                "training_last_date": common_dates[t - 1].isoformat(),
                "first_held_return_date": next_close,
                "weights": dict(zip(symbols, new_weights)),
                "turnover_notional_over_equity": turnover,
                "trade_cost_equity_index_units": cost,
            })
        moves = daily_asset_returns[t]
        holdings = [held * (1 + move) for held, move in zip(holdings, moves)]
        interest = debt * rate_per_day
        debt += interest
        financing_paid += interest
        equity = sum(holdings) - debt
        if equity <= 0:
            raise ValueError(
                f"Simulated account equity depleted by {next_close}; "
                "reduce leverage or change assumptions"
            )
        strategy_daily.append(equity / old_equity - 1.0)
        peak = max(peak, equity)
        drawdown = equity / peak - 1.0
        prev_benchmark = benchmark
        benchmark_holdings = [
            held * (1 + move) for held, move in zip(benchmark_holdings, moves)
        ]
        benchmark = sum(benchmark_holdings)
        benchmark_daily.append(benchmark / prev_benchmark - 1.0)
        benchmark_peak = max(benchmark_peak, benchmark)
        maint = maintenance_fraction * sum(holdings) if leverage > 1 else 0.0
        excess = equity - maint
        breach = leverage > 1 and excess < 0
        if breach:
            margin_breach_days += 1
            if first_margin_breach_date is None:
                first_margin_breach_date = next_close
        curve.append({
            "date": next_close,
            "equity_index": equity,
            "benchmark_equity_index": benchmark,
            "drawdown": drawdown,
            "benchmark_drawdown": benchmark / benchmark_peak - 1,
            "maintenance_excess_equity_index_units": excess,
            "below_assumed_maintenance": breach,
        })

    bench_metrics = _metrics(benchmark_daily, [
        {"drawdown": point["benchmark_drawdown"]} for point in curve
    ], benchmark)
    return {
        "method": "rolling_train_signal_t_minus_1_execute_t_close_hold_next_session",
        "is_trained_ai": False,
        "parameters": {
            "lookback_trading_days": lookback_days,
            "rebalance_every_trading_days": rebalance_days,
            "risk_aversion": risk_aversion,
            "max_weight": max_weight,
            "target_leverage": leverage,
            "annual_margin_interest_rate_assumption": annual_financing_rate,
            "trading_cost_basis_points_each_side": fee_bps,
            "assumed_maintenance_fraction": maintenance_fraction,
        },
        "data": {
            "source_file": str(path),
            "symbols": symbols,
            "shared_price_dates": len(common_dates),
            "first_shared_price_date": common_dates[0].isoformat(),
            "last_shared_price_date": common_dates[-1].isoformat(),
            "first_backtest_return_date": curve[0]["date"],
            "last_backtest_return_date": curve[-1]["date"],
            "backtest_daily_observations": len(curve),
        },
        "strategy": {
            **_metrics(strategy_daily, curve, equity),
            "rebalance_count": len(rebalances),
            "total_trading_fees_equity_index_units": fees_paid,
            "total_financing_interest_equity_index_units": financing_paid,
            "below_assumed_maintenance_days": margin_breach_days,
            "first_assumed_maintenance_breach_date": first_margin_breach_date,
        },
        "benchmark": {
            "type": "equal_weight_buy_and_hold_same_symbols_no_costs",
            **bench_metrics,
        },
        "rebalances": rebalances,
        "equity_curve": curve,
        "warnings": [
            "Research-only walk-forward simulation; historical data is not point-in-time verified.",
            "Signals use closes through t-1; portfolio trades at close t, then earns return from t to t+1.",
            "Equal-weight buy-and-hold benchmark uses the same assets, with zero fees, no borrowing, and no rebalancing.",
            "Constant annual financing rate and bps trading costs are assumed; taxes, spreads, slippage, currency and cash yield are excluded.",
            "Margin thresholds are illustrative only, not broker maintenance rules; forced liquidation is not modeled.",
            "Fixed current symbol universe introduces survivorship/selection bias; model performance does not imply future returns.",
        ],
    }
