"""Cash-only, past-only walk-forward simulation for AtlasPM factor rankings.

Decision: today's completed close. Execution: next benchmark-session close.
P&L: subsequent adjusted-close ratios, with one-way notional cost estimates.
Membership can come from dated snapshots supplied by the caller.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import asdict
from datetime import date
from math import isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from atlaspm.data.historical import load_adjusted_prices
from atlaspm.data.historical_universe import DEFAULT_MAX_UNIVERSE_AGE_DAYS, HistoricalUniverse
from atlaspm.data.stock_delistings import load_stock_delistings
from atlaspm.engine.stock_allocation import (
    StockAllocationSettings, optimize_stock_allocation, prepare_stock_allocation,
)
from atlaspm.research.stock_backtest_portfolio import StockBacktestPortfolio
from atlaspm.strategies.equity.stock_screener import load_fundamental_history, screen_stocks_from_prices


def _ratio_drawdown(values: list[float]) -> float:
    high = 1.0
    worst = 0.0
    for value in values:
        high = max(high, value)
        worst = min(worst, value / high - 1.0)
    return worst


def _performance(curve: list[dict[str, Any]], key: str) -> dict[str, Any]:
    levels = [float(row[key]) for row in curve]
    returns = [b / a - 1.0 if a else 0.0 for a, b in zip(levels, levels[1:])]
    total = levels[-1] / levels[0] - 1.0
    years = len(returns) / 252.0
    vol = stdev(returns) * sqrt(252) if len(returns) > 1 else None
    sharpe = (fmean(returns) * sqrt(252) / stdev(returns)
              if len(returns) > 1 and stdev(returns) > 1e-12 else None)
    return {
        "total_return": total,
        "cagr": (levels[-1] / levels[0]) ** (1.0 / years) - 1 if years > 0 else None,
        "annual_volatility": vol,
        "sharpe_zero_risk_free": sharpe,
        "max_drawdown": _ratio_drawdown(levels),
        "return_observations": len(returns),
    }


def _yearly(curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    annual: dict[str, dict[str, Any]] = {}
    for previous, current in zip(curve, curve[1:]):
        year = current["date"][:4]
        row = annual.setdefault(year, {"year": year, "sessions": 0,
                                       "portfolio_growth": 1.0, "spy_growth": 1.0,
                                       "universe_growth": 1.0})
        if previous["equity_index"]:
            row["portfolio_growth"] *= current["equity_index"] / previous["equity_index"]
        row["spy_growth"] *= current["spy_index"] / previous["spy_index"]
        if previous["universe_index"]:
            row["universe_growth"] *= current["universe_index"] / previous["universe_index"]
        row["sessions"] += 1
    return [{
        "year": r["year"], "sessions": r["sessions"],
        "portfolio_return": r["portfolio_growth"] - 1.0,
        "spy_return": r["spy_growth"] - 1.0,
        "universe_return": r["universe_growth"] - 1.0,
        "excess_percentage_points": 100 * (r["portfolio_growth"] - r["spy_growth"]),
    } for r in annual.values()]


def stock_backtest_schedule(
    benchmark_prices_path: Path,
    benchmark_symbol: str,
    start: str,
    end: str,
    rebalance_days: int,
) -> tuple[dict[date, float], list[date], range]:
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("start/end must use YYYY-MM-DD") from exc
    if first >= last:
        raise ValueError("end must be later than start")
    if type(rebalance_days) is not int or rebalance_days < 1:
        raise ValueError("rebalance-days must be a positive integer")

    ticker = benchmark_symbol.strip().upper()
    if not ticker:
        raise ValueError("Benchmark symbol is required")
    benchmark_all = load_adjusted_prices(benchmark_prices_path)
    if ticker not in benchmark_all:
        raise ValueError(f"Benchmark {ticker} missing in benchmark price file")
    benchmark_prices = benchmark_all[ticker]
    dates = sorted(day for day in benchmark_prices if day <= last)
    first_index = max(252, bisect_left(dates, first))
    last_index = len(dates) - 1
    if first_index + 2 > last_index:
        raise ValueError("Need 253 benchmark warmup sessions and 3+ test sessions")
    if any((b - a).days > 14 for a, b in zip(dates[first_index:-1], dates[first_index + 1:])):
        raise ValueError("Gap in benchmark calendar > 14 days; inspect historical source")

    # Each decision must leave a subsequent session for execution.
    return benchmark_prices, dates, range(first_index, last_index, rebalance_days)


def backtest_stock_screener(
    prices_path: Path,
    benchmark_prices_path: Path,
    *,
    start: str = "2000-01-01",
    end: str = "2026-11-20",
    benchmark_symbol: str = "SPY",
    top: int = 10,
    rebalance_days: int = 21,
    fee_bps: float = 5.0,
    slippage_bps: float = 5.0,
    max_volatility: float = 0.80,
    max_drawdown: float = 0.65,
    require_uptrend: bool = False,
    universe_history_path: Path | None = None,
    max_universe_age_days: int = DEFAULT_MAX_UNIVERSE_AGE_DAYS,
    delistings_path: Path | None = None,
    fundamentals_path: Path | None = None,
    max_fundamental_age_days: int = 200,
    minimum_market_cap_usd: float = 0.0,
    max_per_sector: int | None = None,
    quant_settings: StockAllocationSettings | None = None,
) -> dict[str, Any]:
    """Equal-weight long-only, all-cash if no qualified names.

    Cash earns 0%. Documented delisting events settle outstanding shares in cash.
    Missing closes outside a documented delisting stop the simulation.
    """
    if isinstance(top, bool) or top < 1:
        raise ValueError("top must be a positive integer")
    if not (isfinite(fee_bps) and isfinite(slippage_bps)
            and 0 <= fee_bps <= 100 and 0 <= slippage_bps <= 100):
        raise ValueError("fee-bps and slippage-bps must be finite within 0..100")
    if not (isfinite(max_volatility) and 0 < max_volatility <= 10
            and isfinite(max_drawdown) and 0 < max_drawdown <= 1):
        raise ValueError("Invalid volatility or drawdown cap")
    if quant_settings is not None and quant_settings.sector_cap is not None and fundamentals_path is None:
        raise ValueError("Quant sector cap requires publication-dated fundamentals with sector data")
    ticker = benchmark_symbol.strip().upper()
    price_history = load_adjusted_prices(prices_path)
    price_history.pop(ticker, None)
    if universe_history_path is None and len(price_history) < 2:
        raise ValueError("Need at least two non-benchmark symbols")
    spy_prices, dates, decision_indices = stock_backtest_schedule(
        benchmark_prices_path, ticker, start, end, rebalance_days,
    )
    first_index = decision_indices.start
    last_index = len(dates) - 1
    fundamental_history = (
        load_fundamental_history(fundamentals_path, dates[decision_indices[-1]])
        if fundamentals_path is not None else None
    )
    universe_history = (
        HistoricalUniverse(universe_history_path, max_universe_age_days)
        if universe_history_path is not None else None
    )
    universe_members_seen: set[str] = set()
    delistings = (
        load_stock_delistings(delistings_path, price_history, dates[first_index:])
        if delistings_path is not None else {}
    )

    strategy_account = StockBacktestPortfolio()
    universe_account = StockBacktestPortfolio(baseline=True)
    quant_account = StockBacktestPortfolio() if quant_settings is not None else None
    quant_executions: list[dict[str, Any]] = []
    universe_executions: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    costs_per_dollar = (fee_bps + slippage_bps) / 10_000.0
    start_benchmark_price = spy_prices[dates[first_index]]
    curve: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    for i in range(first_index, last_index + 1):
        observed = dates[i]
        strategy_account.mark(observed, price_history, delistings)
        universe_account.mark(observed, price_history, delistings)
        if quant_account is not None:
            quant_account.mark(observed, price_history, delistings)

        if pending is not None and pending["execution_index"] == i:
            chosen = pending["selected"]
            turnover, fee, unfilled = strategy_account.rebalance(
                chosen, observed, price_history, delistings, costs_per_dollar,
                exact_costs=quant_account is not None,
            )
            orders.append({
                "decision_date": pending["decision_date"],
                "execution_date": observed.isoformat(),
                "selected": list(chosen),
                "unfilled_due_to_delisting": unfilled,
                "turnover_one_way_notional_over_equity": turnover,
                "estimated_transaction_cost_index": fee,
                "equity_after_cost": strategy_account.equity,
            })
            universe_turnover, universe_fee, universe_unfilled = universe_account.rebalance(
                pending["universe_selected"], observed, price_history, delistings, costs_per_dollar,
                exact_costs=quant_account is not None,
            )
            universe_executions.append({
                "decision_date": pending["decision_date"],
                "execution_date": observed.isoformat(),
                "holdings_count": len(universe_account.shares),
                "unfilled_due_to_delisting": universe_unfilled,
                "turnover_ratio_at_execution": universe_turnover,
                "estimated_transaction_cost_index": universe_fee,
                "equity_after_cost": universe_account.equity,
            })
            if quant_account is not None and quant_settings is not None:
                quant_turnover = quant_fee = liquid_turnover = 0.0
                quant_unfilled = []
                quant_status = "INSUFFICIENT_COVARIANCE_HISTORY"
                signal = pending["quant_signal"]
                before_equity = quant_account.equity
                before_liquid = quant_account.liquid_equity
                before_weights = quant_account.liquid_weights
                binding = []
                if before_liquid == 0:
                    quant_status = "NO_LIQUID_EQUITY"
                elif signal is not None:
                    available, quant_unfilled = quant_account.executable_symbols(
                        chosen, observed, price_history, delistings,
                    )
                    notionals, quant_status = optimize_stock_allocation(
                        signal, before_weights, set(available), quant_settings,
                        cost_rate=costs_per_dollar, holding_sessions=rebalance_days,
                    )
                    if notionals is not None:
                        quant_turnover, quant_fee = quant_account.execute_notionals(notionals, costs_per_dollar)
                        liquid_turnover = quant_turnover * (before_equity / before_liquid)
                        after = quant_account.liquid_weights
                        if any(abs(weight - quant_settings.max_weight) <= 1e-7 for weight in after.values()):
                            binding.append("SINGLE_STOCK_CAP")
                        if quant_settings.sector_cap is not None:
                            sector_weights: dict[str | None, float] = {}
                            for symbol, sector in zip(signal.symbols, signal.sectors):
                                sector_weights[sector] = sector_weights.get(sector, 0.0) + after.get(symbol, 0.0)
                            if any(abs(weight - quant_settings.sector_cap) <= 1e-7 for weight in sector_weights.values()):
                                binding.append("SECTOR_CAP")
                        if abs(liquid_turnover - quant_settings.turnover_limit) <= 1e-7:
                            binding.append("TURNOVER_LIMIT")
                quant_executions.append({
                    "decision_date": pending["decision_date"], "execution_date": observed.isoformat(),
                    "selected": list(chosen), "unfilled_due_to_delisting": quant_unfilled,
                    "status": quant_status, "binding_constraints": binding,
                    "pre_trade_liquid_weights": before_weights, "post_trade_liquid_weights": quant_account.liquid_weights,
                    "liquid_equity_before": before_liquid, "liquid_equity_after": quant_account.liquid_equity,
                    "cash_after": quant_account.cash, "cost_index": quant_fee,
                    "turnover_over_liquid_equity": liquid_turnover, "turnover_over_total_equity": quant_turnover,
                })
            pending = None

        if i in decision_indices and i + 1 <= last_index:
            decision_prices = price_history
            snapshot = None
            if universe_history is not None:
                snapshot, decision_prices = universe_history.prices_at(
                    price_history, observed, benchmark_symbol=ticker,
                )
                universe_members_seen.update(decision_prices)
            screen = screen_stocks_from_prices(
                decision_prices, observed.isoformat(),
                top=top, max_annual_volatility=max_volatility,
                max_drawdown=max_drawdown,
                require_uptrend=require_uptrend,
                require_decision_close=True,
                fundamental_history=fundamental_history,
                max_fundamental_age_days=max_fundamental_age_days,
                minimum_market_cap_usd=minimum_market_cap_usd,
                max_per_sector=max_per_sector,
            )
            chosen = [row["symbol"] for row in screen["selected"]]
            # This comparison does not pick the top names: it equally weights
            # *all* symbols with valid 253-close history on the decision date.
            universe_screen = screen_stocks_from_prices(
                decision_prices, observed.isoformat(),
                top=len(decision_prices),
                max_annual_volatility=10.0,
                max_drawdown=1.0,
                require_decision_close=True,
            )
            universe_chosen = [row["symbol"] for row in universe_screen["selected"]]
            decisions.append({
                "date": observed.isoformat(),
                "execution_date": dates[i+1].isoformat(),
                "eligible_count": screen["eligible_count"],
                "excluded_count": len(screen["excluded"]),
                "universe_member_count": len(decision_prices),
                "universe_snapshot_as_of": snapshot.isoformat() if snapshot is not None else None,
                "universe_price_qualified_count": len(universe_chosen),
                "selected": [
                    {"symbol": row["symbol"], "rank": row["rank"], "score": row["score"],
                     "fundamentals_as_of": row["fundamentals_as_of"]}
                    for row in screen["selected"]
                ],
            })
            pending = {
                "decision_date": observed.isoformat(),
                "execution_index": i + 1,
                "selected": chosen,
                "universe_selected": universe_chosen,
            }
            if quant_settings is not None:
                lookback = dates[max(0, i - quant_settings.lookback_sessions):i + 1]
                pending["quant_signal"] = prepare_stock_allocation(
                    screen["selected"], price_history, lookback, quant_settings,
                )

        strategy_account.settle_delistings(observed)
        universe_account.settle_delistings(observed)
        if quant_account is not None:
            quant_account.settle_delistings(observed)
        equity = strategy_account.equity
        universe_equity = universe_account.equity
        curve.append({
            "date": observed.isoformat(),
            "equity_index": equity,
            "spy_index": spy_prices[observed] / start_benchmark_price,
            "universe_index": universe_equity,
            "universe_holdings_count": len(universe_account.shares),
            "universe_cash_weight": universe_account.cash / universe_equity if universe_equity else 0.0,
            "universe_delisting_receivable_index": universe_account.receivable_value,
            "holdings_count": len(strategy_account.shares),
            "cash_weight": strategy_account.cash / equity if equity else 0.0,
            "delisting_receivable_index": strategy_account.receivable_value,
        })
        if quant_account is not None:
            curve[-1]["quant_equity_index"] = quant_account.equity

    portfolio = _performance(curve, "equity_index")
    benchmark = _performance(curve, "spy_index")
    universe_benchmark = _performance(curve, "universe_index")
    ranking_mode = "technical_only" if fundamental_history is None else "fundamental_plus_technical"
    quant_result = {}
    if quant_account is not None and quant_settings is not None:
        quant_result["quant"] = {
            "parameters": asdict(quant_settings), "portfolio": _performance(curve, "quant_equity_index"),
            "sum_turnover_ratios": quant_account.turnover, "total_cost_index": quant_account.cost,
            "executions": quant_executions, "delisting_settlements": quant_account.settlements,
        }
    return {
        **quant_result,
        "method": "offline_stock_factor_walkforward_next_close_v1",
        "requested_window": {"start": start, "end": end},
        "actual_window": {"start": curve[0]["date"], "end": curve[-1]["date"]},
        "warmup": {"minimum_close_sessions": 253,
                   "first_decision_close": decisions[0]["date"],
                   "note": "First usable decision follows 252 prior benchmark sessions."},
        "parameters": {
            "benchmark": ticker, "top": top,
            "rebalance_sessions": rebalance_days,
            "fee_bps_per_traded_dollar": fee_bps,
            "slippage_bps_per_traded_dollar": slippage_bps,
            "maximum_annual_volatility": max_volatility,
            "maximum_drawdown_magnitude": max_drawdown,
            "require_uptrend": require_uptrend,
            "ranking_mode": ranking_mode,
            "delisting_events_in_window": len(delistings),
            "universe_source": "historical_snapshots" if universe_history is not None else "supplied_prices",
            "max_universe_age_days": max_universe_age_days if universe_history is not None else None,
            "max_fundamental_age_days": (
                max_fundamental_age_days if fundamental_history is not None else None
            ),
            "minimum_market_cap_usd": minimum_market_cap_usd,
            "max_per_sector": max_per_sector,
            "long_only": True, "gross_exposure_cap": 1.0,
            "financing_enabled": False,
            "cash_yield_annual": 0.0,
        },
        "universe_count_excluding_benchmark": (
            len(universe_members_seen) if universe_history is not None else len(price_history)
        ),
        "decision_count": len(decisions),
        "executed_rebalances": len(orders),
        "portfolio": portfolio,
        "benchmark": benchmark,
        "equal_weight_universe": universe_benchmark,
        "equal_weight_universe_definition": (
            "Every member of the decision universe with at least 253 recent closes on the "
            "decision date, no sector or volatility filter, equally weighted "
            "and next-close rebalanced with matching fees and slippage."
        ),
        "relative": {
            "cumulative_return_difference_percentage_points":
                100 * (portfolio["total_return"] - benchmark["total_return"]),
            "cagr_difference_percentage_points":
                100 * (portfolio["cagr"] - benchmark["cagr"]),
            "cagr_minus_equal_weight_universe_percentage_points":
                100 * (portfolio["cagr"] - universe_benchmark["cagr"]),
            "cumulative_minus_equal_weight_universe_percentage_points":
                100 * (portfolio["total_return"] - universe_benchmark["total_return"]),
        },
        "execution_summary": {
            "total_turnover_as_multiple_of_initial_equity": strategy_account.turnover,
            "total_modeled_transaction_cost_as_fraction_of_initial_equity": strategy_account.cost,
        },
        "equal_weight_universe_execution_summary": {
            "sum_of_execution_turnover_ratios": universe_account.turnover,
            "sum_of_costs_in_normalized_initial_equity_units": universe_account.cost,
        },
        "delisting_settlements": strategy_account.settlements,
        "equal_weight_delisting_settlements": universe_account.settlements,
        "equal_weight_universe_executions": universe_executions,
        "yearly_returns": _yearly(curve),
        "decisions": decisions,
        "executions": orders,
        "equity_curve": curve,
        "live_execution_enabled": False,
        "warnings": [
            "NOT a trained AI model, proof of alpha or actual/paper broker trading.",
            (
                "Strategy and baseline use the same caller-supplied historical membership; completeness is not independently verified."
                if universe_history is not None else
                "Strategy and baseline use membership inferred from the supplied prices, which can introduce survivorship bias."
            ),
            "Documented cash delistings carry a non-spendable last-close receivable until cash is credited after the modeled close.",
            "Unexplained missing quotes stop the simulation; only documented delistings can cancel a scheduled fill.",
            "The full-universe comparison is 1x equal-weight, next-close rebalanced with matched turnover costs; not a free buy-and-hold portfolio.",
            "Provider-adjusted historical prices may be revised retrospectively; true vintage point-in-time data are not certified.",
            "Supplied fundamentals must use availability dates known by the decision close; data vintages are not independently verified.",
            "No liquidity/borrow/spread conditions, cash yield or tax modeling.",
            "Use untouched holdouts and genuine historically available universe snapshots before evaluating skill.",
            "Signals use decision close; hypothetical execution next close; profits accrue only after execution.",
            "Trading costs are a simple turnover-based approximation, not a fill/execution simulator.",
            "Partial years and 253-close warmup limit coverage; the actual evaluated window is explicitly reported.",
        ],
    }
