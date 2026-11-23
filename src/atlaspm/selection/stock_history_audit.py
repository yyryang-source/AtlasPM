"""Audit historical decision-close coverage before running a stock backtest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from atlaspm.historical import load_adjusted_prices
from atlaspm.selection.historical_universe import DEFAULT_MAX_UNIVERSE_AGE_DAYS, HistoricalUniverse
from atlaspm.selection.stock_walkforward import stock_backtest_schedule


def audit_stock_history(
    prices_path: Path,
    benchmark_prices_path: Path,
    universe_history_path: Path,
    *,
    start: str,
    end: str,
    benchmark_symbol: str = "SPY",
    rebalance_days: int = 21,
    max_universe_age_days: int = DEFAULT_MAX_UNIVERSE_AGE_DAYS,
) -> dict[str, Any]:
    ticker = benchmark_symbol.strip().upper()
    _, dates, decision_indices = stock_backtest_schedule(
        benchmark_prices_path, ticker, start, end, rebalance_days,
    )
    prices = load_adjusted_prices(prices_path)
    historical_universe = HistoricalUniverse(universe_history_path, max_universe_age_days)
    decisions = []
    unavailable_snapshots = 0
    missing_close_count = 0
    expected_close_count = 0
    for index in decision_indices:
        decision = dates[index]
        row = {"decision_date": decision.isoformat(), "next_close_date": dates[index + 1].isoformat()}
        try:
            snapshot, members = historical_universe.members_at(decision, benchmark_symbol=ticker)
        except ValueError as exc:
            unavailable_snapshots += 1
            decisions.append({**row, "status": "UNUSABLE_SNAPSHOT", "error": str(exc)})
            continue

        missing = [symbol for symbol in members if decision not in prices.get(symbol, {})]
        missing_close_count += len(missing)
        expected_close_count += len(members)
        decisions.append({
            **row,
            "status": "MISSING_DECISION_CLOSES" if missing else "COMPLETE",
            "snapshot_as_of": snapshot.isoformat(),
            "member_count": len(members),
            "members_with_decision_close": len(members) - len(missing),
            "missing_decision_close_symbols": missing,
        })

    observed_close_count = expected_close_count - missing_close_count
    return {
        "status": (
            "REVIEW_REQUIRED" if unavailable_snapshots or missing_close_count else "DECISION_CLOSES_COMPLETE"
        ),
        "requested_window": {"start": start, "end": end},
        "actual_window": {"start": dates[decision_indices.start].isoformat(), "end": dates[-1].isoformat()},
        "benchmark_symbol": ticker,
        "rebalance_sessions": rebalance_days,
        "max_universe_age_days": max_universe_age_days,
        "decision_count": len(decisions),
        "decisions_without_usable_snapshot": unavailable_snapshots,
        "expected_member_closes_on_usable_snapshots": expected_close_count,
        "observed_member_closes_on_usable_snapshots": observed_close_count,
        "missing_member_closes_on_usable_snapshots": missing_close_count,
        "decision_close_coverage_pct_on_usable_snapshots": (
            round(100.0 * observed_close_count / expected_close_count, 2) if expected_close_count else None
        ),
        "decisions": decisions,
    }
