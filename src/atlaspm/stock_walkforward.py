"""Cash-only, past-only walk-forward simulation for AtlasPM factor rankings.

Decision: today's completed close. Execution: next benchmark-session close.
P&L: subsequent adjusted-close ratios, with one-way notional cost estimates.
No brokerage, leverage, order intent, training or survivorship-safe universe.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date
from math import isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from atlaspm.historical import load_adjusted_prices
from atlaspm.stock_screener import screen_stocks_from_prices


def _ratio_drawdown(values: list[float]) -> float:
    high = 1.0
    worst = 0.0
    for value in values:
        high = max(high, value)
        worst = min(worst, value / high - 1.0)
    return worst


def _performance(curve: list[dict[str, Any]], key: str) -> dict[str, Any]:
    levels = [float(row[key]) for row in curve]
    returns = [b / a - 1.0 for a, b in zip(levels, levels[1:])]
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
                                       "portfolio_growth": 1.0, "spy_growth": 1.0})
        row["portfolio_growth"] *= current["equity_index"] / previous["equity_index"]
        row["spy_growth"] *= current["spy_index"] / previous["spy_index"]
        row["universe_growth"] = row.get("universe_growth", 1.0) * (
            current["universe_index"] / previous["universe_index"]
        )
        row["sessions"] += 1
    return [{
        "year": r["year"], "sessions": r["sessions"],
        "portfolio_return": r["portfolio_growth"] - 1.0,
        "spy_return": r["spy_growth"] - 1.0,
        "universe_return": r["universe_growth"] - 1.0,
        "excess_percentage_points": 100 * (r["portfolio_growth"] - r["spy_growth"]),
    } for r in annual.values()]


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
) -> dict[str, Any]:
    """Equal-weight long-only, all-cash if no qualified names.

    Cash yield is 0%; no financing or shorts. A selected or held symbol missing
    a required market close raises ValueError rather than forward-filling or
    borrowing future prices. Backtest is retrospective research, not trading.
    """
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("start/end must use YYYY-MM-DD") from exc
    if first >= last:
        raise ValueError("end must be later than start")
    if isinstance(top, bool) or isinstance(rebalance_days, bool) or top < 1 or rebalance_days < 1:
        raise ValueError("top and rebalance-days must be positive integers")
    if not (isfinite(fee_bps) and isfinite(slippage_bps)
            and 0 <= fee_bps <= 100 and 0 <= slippage_bps <= 100):
        raise ValueError("fee-bps and slippage-bps must be finite within 0..100")
    if not (isfinite(max_volatility) and 0 < max_volatility <= 10
            and isfinite(max_drawdown) and 0 < max_drawdown <= 1):
        raise ValueError("Invalid volatility or drawdown cap")
    ticker = benchmark_symbol.strip().upper()
    if not ticker:
        raise ValueError("Benchmark symbol is required")

    price_history = load_adjusted_prices(prices_path)
    benchmark_all = load_adjusted_prices(benchmark_prices_path)
    if ticker not in benchmark_all:
        raise ValueError(f"Benchmark {ticker} missing in benchmark price file")
    if ticker in price_history:
        price_history.pop(ticker)  # Never recommend the comparison benchmark itself.
    if len(price_history) < 2:
        raise ValueError("Need at least two non-benchmark symbols")
    spy_prices = benchmark_all[ticker]
    dates = sorted(d for d in spy_prices if d <= last)
    first_index = max(252, bisect_left(dates, first))
    last_index = bisect_right(dates, last) - 1
    if first_index + 2 > last_index:
        raise ValueError("Need 253 benchmark warmup sessions and 3+ test sessions")
    if any((b - a).days > 14 for a, b in zip(dates[first_index:last_index],
                                              dates[first_index+1:last_index+1])):
        raise ValueError("Gap in benchmark calendar > 14 days; inspect historical source")
    decision_indices = set(range(first_index, last_index, rebalance_days))
    # Executions are always one benchmark session after a completed decision close.
    if not decision_indices:
        raise ValueError("No eligible decision dates")

    equity = 1.0
    cash = 1.0
    shares: dict[str, float] = {}
    # Independently capitalized 1x cash ledger for the entire historically
    # price-qualified universe (no factor rank/risk threshold exclusion).
    universe_cash = 1.0
    universe_shares: dict[str, float] = {}
    universe_equity = 1.0
    universe_turnover_sum = 0.0
    universe_cost_sum = 0.0
    universe_executions: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    turnover_sum = 0.0
    cost_sum = 0.0
    costs_per_dollar = (fee_bps + slippage_bps) / 10_000.0
    start_benchmark_price = spy_prices[dates[first_index]]
    curve: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    for i in range(first_index, last_index + 1):
        observed = dates[i]
        marks: dict[str, float] = {}
        for symbol in shares:
            price = price_history[symbol].get(observed)
            if price is None:
                raise ValueError(f"MISSING_HELD_PRICE: {symbol} {observed}; no forward fill allowed")
            marks[symbol] = price
        equity = cash + sum(quantity * marks[symbol] for symbol, quantity in shares.items())
        if not isfinite(equity) or equity <= 0:
            raise ValueError(f"Invalid equity on {observed}")
        universe_marks: dict[str, float] = {}
        for symbol in universe_shares:
            price = price_history[symbol].get(observed)
            if price is None:
                raise ValueError(
                    f"MISSING_UNIVERSE_HELD_PRICE: {symbol} {observed}; no forward fill"
                )
            universe_marks[symbol] = price
        universe_equity = universe_cash + sum(
            quantity * universe_marks[symbol]
            for symbol, quantity in universe_shares.items()
        )
        if not isfinite(universe_equity) or universe_equity <= 0:
            raise ValueError(f"Invalid universe baseline equity on {observed}")

        if pending is not None and pending["execution_index"] == i:
            chosen = pending["selected"]
            # Forbid using a future close to decide membership; use it ONLY to
            # price an already-scheduled next-close hypothetical execution.
            for symbol in chosen:
                close = price_history[symbol].get(observed)
                if close is None:
                    raise ValueError(
                        f"MISSING_EXECUTION_PRICE: {symbol} {observed}; no future data substitution"
                    )
                marks[symbol] = close
            fraction = 1.0 / len(chosen) if chosen else 0.0
            current = {symbol: qty * marks[symbol] / equity
                       for symbol, qty in shares.items()}
            target = {symbol: fraction for symbol in chosen}
            turnover = sum(abs(target.get(s, 0.0) - current.get(s, 0.0))
                           for s in set(current) | set(target))
            fee = equity * turnover * costs_per_dollar
            if fee >= equity:
                raise ValueError("Transaction cost exceeded modeled equity")
            after_fee = equity - fee
            shares = {symbol: (after_fee * fraction / marks[symbol]) for symbol in chosen}
            cash = after_fee - sum(shares[s] * marks[s] for s in chosen)
            # Decimal rounding noise is permitted around zero, not leverage.
            if cash < -1e-10:
                raise ValueError("Simulated holdings exceed equity")
            cash = max(0.0, cash)
            equity = cash + sum(shares[s] * marks[s] for s in shares)
            cost_sum += fee
            turnover_sum += turnover
            orders.append({
                "decision_date": pending["decision_date"],
                "execution_date": observed.isoformat(),
                "selected": list(chosen),
                "turnover_one_way_notional_over_equity": turnover,
                "estimated_transaction_cost_index": fee,
                "equity_after_cost": equity,
            })
            # Same decision and next-close execution for the separate
            # full-universe equal-weight comparison. No strategy ranking
            # enters its membership or target weights.
            universe_chosen = pending["universe_selected"]
            for symbol in universe_chosen:
                price = price_history[symbol].get(observed)
                if price is None:
                    raise ValueError(
                        f"MISSING_UNIVERSE_EXECUTION_PRICE: {symbol} {observed}"
                    )
                universe_marks[symbol] = price
            universe_fraction = 1.0 / len(universe_chosen) if universe_chosen else 0.0
            universe_current = {
                symbol: quantity * universe_marks[symbol] / universe_equity
                for symbol, quantity in universe_shares.items()
            }
            universe_target = {symbol: universe_fraction for symbol in universe_chosen}
            universe_turnover = sum(
                abs(universe_target.get(s, 0.0) - universe_current.get(s, 0.0))
                for s in set(universe_current) | set(universe_target)
            )
            universe_fee = universe_equity * universe_turnover * costs_per_dollar
            if universe_fee >= universe_equity:
                raise ValueError("Universe comparison transaction cost exceeded equity")
            universe_after_fee = universe_equity - universe_fee
            universe_shares = {
                symbol: universe_after_fee * universe_fraction / universe_marks[symbol]
                for symbol in universe_chosen
            }
            universe_cash = universe_after_fee - sum(
                universe_shares[s] * universe_marks[s] for s in universe_shares
            )
            if universe_cash < -1e-10:
                raise ValueError("Universe comparison exceeds 1x exposure")
            universe_cash = max(0.0, universe_cash)
            universe_equity = universe_cash + sum(
                universe_shares[s] * universe_marks[s] for s in universe_shares
            )
            universe_turnover_sum += universe_turnover
            universe_cost_sum += universe_fee
            universe_executions.append({
                "decision_date": pending["decision_date"],
                "execution_date": observed.isoformat(),
                "holdings_count": len(universe_chosen),
                "turnover_ratio_at_execution": universe_turnover,
                "estimated_transaction_cost_index": universe_fee,
                "equity_after_cost": universe_equity,
            })
            pending = None

        if i in decision_indices and i + 1 <= last_index:
            screen = screen_stocks_from_prices(
                price_history, observed.isoformat(),
                top=top, max_annual_volatility=max_volatility,
                max_drawdown=max_drawdown,
                require_uptrend=require_uptrend,
                require_decision_close=True,
            )
            chosen = [row["symbol"] for row in screen["selected"]]
            # This comparison does not pick the top names: it equally weights
            # *all* symbols with valid 253-close history on the decision date.
            # Risk filters are deliberately disabled; delisted companies are
            # still absent unless supplied in the CSV.
            universe_screen = screen_stocks_from_prices(
                price_history, observed.isoformat(),
                top=len(price_history),
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
                "universe_price_qualified_count": len(universe_chosen),
                "selected": [
                    {"symbol": row["symbol"], "rank": row["rank"], "score": row["score"]}
                    for row in screen["selected"]
                ],
            })
            pending = {
                "decision_date": observed.isoformat(),
                "execution_index": i + 1,
                "selected": chosen,
                "universe_selected": universe_chosen,
            }

        curve.append({
            "date": observed.isoformat(),
            "equity_index": equity,
            "spy_index": spy_prices[observed] / start_benchmark_price,
            "universe_index": universe_equity,
            "universe_holdings_count": len(universe_shares),
            "universe_cash_weight": universe_cash / universe_equity,
            "holdings_count": len(shares),
            "cash_weight": cash / equity,
        })

    portfolio = _performance(curve, "equity_index")
    benchmark = _performance(curve, "spy_index")
    universe_benchmark = _performance(curve, "universe_index")
    return {
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
            "long_only": True, "gross_exposure_cap": 1.0,
            "financing_enabled": False,
            "cash_yield_annual": 0.0,
        },
        "universe_count_excluding_benchmark": len(price_history),
        "decision_count": len(decisions),
        "executed_rebalances": len(orders),
        "portfolio": portfolio,
        "benchmark": benchmark,
        "equal_weight_universe": universe_benchmark,
        "equal_weight_universe_definition": (
            "Every supplied ticker with at least 253 recent closes on the "
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
            "total_turnover_as_multiple_of_initial_equity": turnover_sum,
            "total_modeled_transaction_cost_as_fraction_of_initial_equity": cost_sum,
        },
        "equal_weight_universe_execution_summary": {
            "sum_of_execution_turnover_ratios": universe_turnover_sum,
            "sum_of_costs_in_normalized_initial_equity_units": universe_cost_sum,
        },
        "equal_weight_universe_executions": universe_executions,
        "yearly_returns": _yearly(curve),
        "decisions": decisions,
        "executions": orders,
        "equity_curve": curve,
        "live_execution_enabled": False,
        "warnings": [
            "NOT a trained AI model, proof of alpha or actual/paper broker trading.",
            "Both strategy and equal-weight universe use today's surviving ticker list; neither corrects survivorship bias.",
            "Eligible universe composition evolves as stocks reach 253 recorded prices, not historical index memberships.",
            "The full-universe comparison is 1x equal-weight, next-close rebalanced with matched turnover costs; not a free buy-and-hold portfolio.",
            "Provider-adjusted historical prices may be revised retrospectively; true vintage point-in-time data are not certified.",
            "No company fundamental dates, sector caps, liquidity/borrow/spread conditions, cash yield or tax modeling.",
            "Use untouched holdouts and genuine historically available universe snapshots before evaluating skill.",
            "Signals use decision close; hypothetical execution next close; profits accrue only after execution.",
            "Trading costs are a simple turnover-based approximation, not a fill/execution simulator.",
            "Partial years and 253-close warmup limit coverage; the actual evaluated window is explicitly reported.",
        ],
    }
