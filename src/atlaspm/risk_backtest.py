"""Past-only risk-budget rebalancing simulation with explicit cash and debt.

Research-only: decisions use price observations ending before execution.
No live brokerage interaction, no dynamic broker liquidation, no alpha claim.
"""

from __future__ import annotations

from math import isfinite, sqrt
from pathlib import Path
from typing import Any

from atlaspm.backtest import _metrics, _sample_estimates
from atlaspm.historical import TRADING_DAYS_PER_YEAR, load_adjusted_prices
from atlaspm.mean_variance import optimize_mean_variance


def _fraction(value: object, name: str, minimum: float = 0.0,
              maximum: float | None = None, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    value = float(value)
    if not isfinite(value) or value < minimum or (positive and value == minimum):
        raise ValueError(f"{name} is outside valid range")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} is outside valid range")
    return value


def choose_budgeted_exposure(
    weights: dict[str, float],
    covariance: list[list[float]],
    *,
    maximum_requested_leverage: float,
    max_annual_volatility: float,
    stress_decline: float,
    max_stress_loss: float,
    maintenance_fraction: float,
    minimum_maintenance_buffer: float,
    annual_financing_rate: float,
    max_annual_financing_cost: float,
) -> dict[str, Any]:
    """Select gross exposure 0..requested, including a cash sleeve below 1x.

    Assumes long-only weights sum to 1, and covariance matches weight order.
    Holding composition comes from optimizer; this just scales risk exposure.
    """
    from atlaspm.mean_variance import _validate_covariance
    if not weights or abs(sum(weights.values()) - 1) > 1e-8:
        raise ValueError("Risk-sleeve weights must sum to 1")
    symbols = list(weights)
    if any(not isfinite(w) or w < 0 for w in weights.values()):
        raise ValueError("Invalid risk-sleeve weights")
    matrix = _validate_covariance(covariance, len(symbols))
    leverage = _fraction(maximum_requested_leverage, "maximum_requested_leverage", 1, 4)
    volcap = _fraction(max_annual_volatility, "max_annual_volatility", 0, positive=True)
    shock = _fraction(stress_decline, "stress_decline", 0, 1)
    losscap = _fraction(max_stress_loss, "max_stress_loss", 0, 1)
    maintenance = _fraction(maintenance_fraction, "maintenance_fraction", 0, 1, positive=True)
    buffer = _fraction(minimum_maintenance_buffer, "minimum_maintenance_buffer", 0, 1)
    apr = _fraction(annual_financing_rate, "annual_financing_rate")
    financing_cap = _fraction(max_annual_financing_cost, "max_annual_financing_cost")
    w = list(weights.values())
    variance = sum(w[i] * matrix[i][j] * w[j]
                   for i in range(len(w)) for j in range(len(w)))
    annual_sigma = sqrt(max(0, variance))
    vol_bound = volcap / annual_sigma if annual_sigma > 0 else float("inf")
    loss_bound = losscap / shock if shock > 0 else float("inf")
    cash_buffer_bound = (1 - buffer) / shock if shock > 0 else float("inf")
    maintenance_denom = shock + maintenance * (1 - shock)
    margin_bound = ((1 - buffer) / maintenance_denom
                    if maintenance_denom > 0 else float("inf"))
    interest_bound = (1 + financing_cap / apr if apr > 0 else float("inf"))
    cash_range = max(0.0, min(1.0, leverage, vol_bound, loss_bound, cash_buffer_bound))
    margin_range = min(leverage, vol_bound, loss_bound, margin_bound, interest_bound)
    gross = margin_range if margin_range > 1.0 + 1e-10 else cash_range
    gross = min(leverage, max(0.0, gross))
    stress_equity = 1 - gross * shock
    stressed_maint = (
        maintenance * gross * (1 - shock) if gross > 1.0 else 0.0
    )
    annual_cost = max(0.0, gross - 1.0) * apr
    if gross * annual_sigma > volcap + 1e-9:
        raise ValueError("Volatility budget violated by selected exposure")
    if gross * shock > losscap + 1e-9:
        raise ValueError("Stress-loss budget violated by selected exposure")
    if stress_equity - stressed_maint < buffer - 1e-9:
        raise ValueError("Maintenance buffer budget violated by selected exposure")
    if annual_cost > financing_cap + 1e-9:
        raise ValueError("Financing budget violated by selected exposure")
    bounds = {
        "requested_maximum": leverage,
        "annual_volatility": vol_bound,
        "uniform_stress_loss": loss_bound,
        "unlevered_cash_buffer": cash_buffer_bound,
        "financed_maintenance_buffer": margin_bound,
        "financing_cost": interest_bound,
    }
    # Comparison to each bound is descriptive because some are regime-specific.
    return {
        "selected_gross_exposure": gross,
        "cash_weight": max(0.0, 1 - gross),
        "estimated_risky_sleeve_annual_volatility": annual_sigma,
        "estimated_total_annual_volatility": gross * annual_sigma,
        "uniform_stress_equity_fraction": stress_equity,
        "uniform_stress_maintenance_excess_fraction": stress_equity - stressed_maint,
        "annual_borrowing_cost_fraction": annual_cost,
        "bounds": {k: (v if isfinite(v) else None) for k, v in bounds.items()},
        "notes": "Risk bounds estimated only from past returns at signal time; not broker buying-power limits.",
    }


def _trade_to_risk_budget(
    existing: list[float], equity: float, weights: list[float],
    gross: float, fee_fraction: float,
) -> tuple[list[float], float, float, float, float]:
    """Solve fees and reconcile holdings+cash-debt to post-fee equity."""
    if equity <= 0:
        raise ValueError("Cannot trade without positive equity")
    fee = 0.0
    for _ in range(100):
        post_fee = equity - fee
        if post_fee <= 0:
            raise ValueError("Trading costs exhaust equity")
        proposed = [post_fee * gross * w for w in weights]
        next_fee = fee_fraction * sum(abs(a - b) for a, b in zip(proposed, existing))
        if abs(next_fee - fee) < 1e-14:
            fee = next_fee
            break
        fee = next_fee
    post_fee = equity - fee
    holdings = [post_fee * gross * w for w in weights]
    loan = max(0.0, (gross - 1.0) * post_fee)
    cash = max(0.0, (1.0 - gross) * post_fee)
    if abs(sum(holdings) + cash - loan - post_fee) > 1e-9 * max(1, equity):
        raise ValueError("Cash/debt/holdings ledger did not reconcile")
    turnover = sum(abs(a - b) for a, b in zip(holdings, existing)) / equity
    return holdings, cash, loan, fee, turnover


def backtest_risk_budget(
    path: Path,
    *,
    lookback_days: int = 756,
    rebalance_days: int = 63,
    risk_aversion: float = 4.0,
    max_weight: float = 0.60,
    max_leverage: float = 2.0,
    max_annual_volatility: float = 0.30,
    stress_decline: float = 0.30,
    max_stress_loss: float = 0.40,
    maintenance_fraction: float = 0.30,
    minimum_maintenance_buffer: float = 0.05,
    annual_financing_rate: float = 0.065,
    max_annual_financing_cost: float = 0.08,
    fee_bps: float = 5.0,
) -> dict[str, Any]:
    """Roll forward: past-only signal at t-1, execute t, earn t->t+1."""
    for label, value, minimum in (
        ("lookback_days", lookback_days, 3),
        ("rebalance_days", rebalance_days, 1),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{label} must be an integer >= {minimum}")
    _fraction(risk_aversion, "risk_aversion", 0, positive=True)
    _fraction(max_weight, "max_weight", 0, 1, positive=True)
    _fraction(fee_bps, "fee_bps", 0, 1000)
    # Check all budget inputs BEFORE the first trading session.
    _fraction(max_leverage, "max_leverage", 1, 4)
    _fraction(max_annual_volatility, "max_annual_volatility", 0, positive=True)
    _fraction(stress_decline, "stress_decline", 0, 1)
    _fraction(max_stress_loss, "max_stress_loss", 0, 1)
    _fraction(maintenance_fraction, "maintenance_fraction", 0, 1, positive=True)
    _fraction(minimum_maintenance_buffer, "minimum_maintenance_buffer", 0, 1)
    _fraction(annual_financing_rate, "annual_financing_rate")
    _fraction(max_annual_financing_cost, "max_annual_financing_cost")
    prices = load_adjusted_prices(path)
    symbols = sorted(prices)
    dates = sorted(set.intersection(*(set(prices[s]) for s in symbols)))
    if len(dates) <= lookback_days + 2:
        raise ValueError("Not enough common dates for past-only training and holdout")
    rows = [[prices[s][d] for s in symbols] for d in dates]
    returns = [
        [rows[i + 1][j] / rows[i][j] - 1 for j in range(len(symbols))]
        for i in range(len(rows) - 1)
    ]
    account_equity = 1.0
    holdings = [0.0] * len(symbols)
    cash, loan = 1.0, 0.0
    benchmark_positions = [1.0 / len(symbols)] * len(symbols)
    benchmark_equity = 1.0
    peak, benchmark_peak = 1.0, 1.0
    account_daily: list[float] = []
    benchmark_daily: list[float] = []
    curve: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    fees_total = 0.0
    interest_total = 0.0
    breach_dates: list[str] = []
    risk_limit_failures_after_hold: list[str] = []

    for t in range(lookback_days + 1, len(dates) - 1):
        old_equity = account_equity
        execution = dates[t].isoformat()
        held_date = dates[t + 1].isoformat()
        if (t - lookback_days - 1) % rebalance_days == 0:
            training_window = returns[t - lookback_days - 1:t - 1]
            estimated, cov = _sample_estimates(training_window, symbols)
            optimized = optimize_mean_variance(
                estimated, cov, leverage=max_leverage,
                risk_aversion=risk_aversion, max_weight=max_weight,
                annual_financing_rate=annual_financing_rate,
            )
            gross_weights = optimized["weights"]
            selected = choose_budgeted_exposure(
                gross_weights, cov,
                maximum_requested_leverage=max_leverage,
                max_annual_volatility=max_annual_volatility,
                stress_decline=stress_decline,
                max_stress_loss=max_stress_loss,
                maintenance_fraction=maintenance_fraction,
                minimum_maintenance_buffer=minimum_maintenance_buffer,
                annual_financing_rate=annual_financing_rate,
                max_annual_financing_cost=max_annual_financing_cost,
            )
            target = selected["selected_gross_exposure"]
            holdings, cash, loan, fees, turnover = _trade_to_risk_budget(
                holdings, account_equity,
                [gross_weights[s] for s in symbols], target, fee_bps / 10000.0
            )
            account_equity -= fees
            fees_total += fees
            decisions.append({
                "signal_date": dates[t - 1].isoformat(),
                "training_start_date": dates[t - lookback_days - 1].isoformat(),
                "training_last_date": dates[t - 1].isoformat(),
                "execution_date": execution,
                "first_held_return_date": held_date,
                "weights": gross_weights,
                "gross_exposure_at_execution": target,
                "cash_weight_at_execution": selected["cash_weight"],
                "estimated_total_annual_volatility": selected["estimated_total_annual_volatility"],
                "uniform_stress_equity_fraction": selected["uniform_stress_equity_fraction"],
                "uniform_stress_maintenance_excess_fraction": selected["uniform_stress_maintenance_excess_fraction"],
                "annual_borrowing_cost_fraction": selected["annual_borrowing_cost_fraction"],
                "bounds": selected["bounds"],
                "turnover_notional_over_equity": turnover,
                "trade_cost_equity_index_units": fees,
            })
        move = returns[t]
        holdings = [h * (1 + r) for h, r in zip(holdings, move)]
        interest = loan * annual_financing_rate / TRADING_DAYS_PER_YEAR
        loan += interest
        interest_total += interest
        account_equity = sum(holdings) + cash - loan
        if account_equity <= 0:
            raise ValueError(
                f"Risk budget account equity depleted at {held_date}; "
                "the historical risk budget did not prevent insolvency"
            )
        account_daily.append(account_equity / old_equity - 1)
        peak = max(peak, account_equity)
        prior_benchmark = benchmark_equity
        benchmark_positions = [
            h * (1 + r) for h, r in zip(benchmark_positions, move)
        ]
        benchmark_equity = sum(benchmark_positions)
        benchmark_daily.append(benchmark_equity / prior_benchmark - 1)
        benchmark_peak = max(benchmark_peak, benchmark_equity)
        required = maintenance_fraction * sum(holdings) if loan > 0 else 0.0
        excess = account_equity - required
        breach = loan > 0 and excess < 0
        if breach:
            breach_dates.append(held_date)
        curve.append({
            "date": held_date,
            "equity_index": account_equity,
            "benchmark_equity_index": benchmark_equity,
            "drawdown": account_equity / peak - 1,
            "benchmark_drawdown": benchmark_equity / benchmark_peak - 1,
            "cash_equity_index_units": cash,
            "loan_equity_index_units": loan,
            "gross_exposure_equity_index_units": sum(holdings),
            "maintenance_excess_equity_index_units": excess,
            "below_assumed_maintenance": breach,
        })

    strategy = _metrics(account_daily, curve, account_equity)
    benchmark = _metrics(
        benchmark_daily,
        [{"drawdown": p["benchmark_drawdown"]} for p in curve],
        benchmark_equity,
    )
    exposures = [d["gross_exposure_at_execution"] for d in decisions]
    return {
        "method": "past_only_rolling_mean_variance_with_cash_and_stress_budgets",
        "is_trained_ai": False,
        "parameters": {
            "lookback_trading_days": lookback_days,
            "rebalance_every_trading_days": rebalance_days,
            "risk_aversion": risk_aversion,
            "max_weight": max_weight,
            "target_leverage": max_leverage,
            "risk_policy": "adaptive_cash_and_stress_budgets",
            "max_annual_volatility": max_annual_volatility,
            "stress_decline": stress_decline,
            "max_stress_loss": max_stress_loss,
            "assumed_maintenance_fraction": maintenance_fraction,
            "minimum_maintenance_buffer": minimum_maintenance_buffer,
            "annual_margin_interest_rate_assumption": annual_financing_rate,
            "max_annual_financing_cost": max_annual_financing_cost,
            "trading_cost_basis_points_each_side": fee_bps,
        },
        "data": {
            "source_file": str(path),
            "symbols": symbols,
            "shared_price_dates": len(dates),
            "first_shared_price_date": dates[0].isoformat(),
            "last_shared_price_date": dates[-1].isoformat(),
            "first_backtest_return_date": curve[0]["date"],
            "last_backtest_return_date": curve[-1]["date"],
            "backtest_daily_observations": len(curve),
        },
        "strategy": {
            **strategy,
            "rebalance_count": len(decisions),
            "total_trading_fees_equity_index_units": fees_total,
            "total_financing_interest_equity_index_units": interest_total,
            "below_assumed_maintenance_days": len(breach_dates),
            "first_assumed_maintenance_breach_date": breach_dates[0] if breach_dates else None,
            "minimum_selected_gross_exposure": min(exposures),
            "maximum_selected_gross_exposure": max(exposures),
            "average_selected_gross_exposure": sum(exposures) / len(exposures),
            "cash_allocation_rebalance_count": sum(g < 1 - 1e-9 for g in exposures),
            "margin_allocation_rebalance_count": sum(g > 1 + 1e-9 for g in exposures),
        },
        "benchmark": {
            "type": "equal_weight_buy_and_hold_same_symbols_no_costs",
            **benchmark,
        },
        "rebalances": decisions,
        "equity_curve": curve,
        "warnings": [
            "Past-only rolling signals, next-close hypothetical execution; not a trained AI or a trade system.",
            "Static historical variance estimates may fail in correlated crashes or regime shifts.",
            "Risk limits apply at scheduled rebalances, not continuously between trades.",
            "Cash earns zero interest and the financing APR stays constant; taxes, spreads and real trade execution are not modeled.",
            "Maintenance rules are constant illustrative fractions; no broker liquidation or intraday enforcement is modeled.",
            "Current symbols were selected with hindsight; historical simulations cannot establish future outperformance.",
        ],
    }
