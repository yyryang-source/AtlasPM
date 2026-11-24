"""Matched, chronologically separated stock selection experiments."""

from __future__ import annotations

from datetime import date
from hashlib import file_digest, sha256
from importlib.metadata import version
from pathlib import Path
from typing import Any

from atlaspm.data.historical import load_adjusted_prices
from atlaspm.research.ai_stock_ranker import _matrix
from atlaspm.research.ai_stock_ranker_v2 import _rank_queries, _rank_scores, fit_rank_queries
from atlaspm.data.historical_universe import DEFAULT_MAX_UNIVERSE_AGE_DAYS, HistoricalUniverse
from atlaspm.research.stock_backtest_portfolio import StockBacktestPortfolio
from atlaspm.data.stock_delistings import StockDelisting, load_stock_delistings
from atlaspm.strategies.equity.stock_screener import (
    COMPARISON_FEATURES, load_fundamental_history, rank_factor_rows, screen_stocks_from_prices,
)
from atlaspm.research.stock_walkforward import _performance, stock_backtest_schedule


def _input_hashes(paths: dict[str, Path]) -> dict[str, str]:
    hashes = {}
    for name, path in paths.items():
        with path.open("rb") as handle:
            hashes[name] = file_digest(handle, "sha256").hexdigest()
    return hashes


def _model_rows(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"symbol": row["symbol"], "features": [row["factors"][feature] for feature in COMPARISON_FEATURES]}
        for row in ranked
    ]


def _forward_return(
    prices: dict[date, float], entry: date, exit_day: date, delisting: StockDelisting | None,
) -> float | None:
    entry_close = prices.get(entry)
    if delisting is not None and delisting.last_trade_date < entry:
        return 0.0  # The simulation leaves the unfilled allocation in cash.
    if entry_close is None:
        return None
    exit_close = prices.get(exit_day)
    if exit_close is None and delisting is not None and delisting.cash_date <= exit_day:
        exit_close = delisting.last_close * (1.0 + delisting.return_from_last_close)
    return exit_close / entry_close - 1.0 if exit_close is not None else None


def _select_unambiguous_top(
    rows: list[dict[str, Any]], scores: Any, top: int,
) -> tuple[list[str], bool]:
    if not rows:
        return [], False
    selected, diagnostics = _rank_scores(rows, scores, top)
    boundary_tie = diagnostics["ties_cross_top_n_cutoff"]
    if boundary_tie:
        selected = selected[:selected[-1]["rank"] - 1]
    return [row["symbol"] for row in selected], boundary_tie


def compare_stock_selection(
    prices_path: Path,
    benchmark_path: Path,
    universe_path: Path,
    fundamentals_path: Path,
    *,
    train_start: str,
    train_end: str,
    validation_end: str,
    test_start: str,
    test_end: str,
    benchmark_symbol: str = "SPY",
    delistings_path: Path | None = None,
    top: int = 10,
    rebalance_days: int = 21,
    fee_bps: float = 5.0,
    slippage_bps: float = 5.0,
    max_universe_age_days: int = DEFAULT_MAX_UNIVERSE_AGE_DAYS,
    max_fundamental_age_days: int = 200,
    num_boost_round: int = 300,
    min_train_rows: int = 300,
) -> dict[str, Any]:
    start_fit, cutoff, end_validation, start_test, end_test = map(
        date.fromisoformat, (train_start, train_end, validation_end, test_start, test_end),
    )
    if not start_fit < cutoff < end_validation < start_test < end_test:
        raise ValueError("Require train-start < train-end < validation-end < test-start < test-end")
    if not (1 <= top <= 1000 and 10 <= num_boost_round <= 2000 and 30 <= min_train_rows <= 1000000):
        raise ValueError("Require top 1..1000, num-boost-round 10..2000 and min-train-rows 30..1000000")
    if not (0 <= fee_bps <= 100 and 0 <= slippage_bps <= 100):
        raise ValueError("fee-bps and slippage-bps must be finite within 0..100")

    paths = {"prices": prices_path, "benchmark": benchmark_path,
             "universe": universe_path, "fundamentals": fundamentals_path}
    if delistings_path is not None:
        paths["delistings"] = delistings_path
    input_hashes = _input_hashes(paths)
    ticker = benchmark_symbol.strip().upper()
    benchmark, test_dates, test_indices = stock_backtest_schedule(
        benchmark_path, ticker, test_start, test_end, rebalance_days,
    )
    _, fit_dates, fit_indices = stock_backtest_schedule(
        benchmark_path, ticker, train_start, validation_end, rebalance_days,
    )
    prices = load_adjusted_prices(prices_path)
    prices.pop(ticker, None)
    historical_universe = HistoricalUniverse(universe_path, max_universe_age_days)
    fundamentals = load_fundamental_history(fundamentals_path, test_dates[test_indices[-1]])
    delistings = load_stock_delistings(delistings_path, prices, test_dates) if delistings_path is not None else {}

    def cohort(day: date) -> tuple[date, list[dict[str, Any]]]:
        snapshot, decision_prices = historical_universe.prices_at(prices, day, benchmark_symbol=ticker)
        ranking = screen_stocks_from_prices(
            decision_prices, day.isoformat(), top=len(decision_prices), require_decision_close=True,
            fundamental_history=fundamentals, max_fundamental_age_days=max_fundamental_age_days,
        )
        return snapshot, ranking["ranked"]

    fit_data: dict[str, list[dict[str, Any]]] = {"train": [], "validation": []}
    incomplete_cohorts = {"train": 0, "validation": 0}
    purged_cohorts = 0
    for index in fit_indices:
        decision = fit_dates[index]
        end_index = index + 1 + rebalance_days
        split = "train" if decision <= cutoff else "validation"
        if end_index >= len(fit_dates) or (split == "train" and fit_dates[end_index] > cutoff):
            purged_cohorts += 1
            continue
        entry, exit_day = fit_dates[index + 1], fit_dates[end_index]
        _, ranked = cohort(decision)
        labeled = []
        benchmark_return = benchmark[exit_day] / benchmark[entry] - 1.0
        for row in _model_rows(ranked):
            symbol = row["symbol"]
            outcome = _forward_return(prices[symbol], entry, exit_day, delistings.get(symbol))
            if outcome is None:
                break
            labeled.append({**row, "date": decision.isoformat(), "label_exit_date": exit_day.isoformat(),
                            "forward_excess_return": outcome - benchmark_return})
        if len(labeled) != len(ranked) or len(labeled) < 3:
            incomplete_cohorts[split] += 1
            continue
        fit_data[split].extend(labeled)

    for split, minimum in (("train", min_train_rows), ("validation", 30)):
        if len(fit_data[split]) < minimum:
            raise ValueError(f"Insufficient complete matched {split} rows: {len(fit_data[split])} < {minimum}")
    queries = {split: _rank_queries(rows) for split, rows in fit_data.items()}
    model, iteration, parameters = fit_rank_queries(
        queries["train"], queries["validation"], feature_names=COMPARISON_FEATURES,
        num_boost_round=num_boost_round,
    )

    runs = {name: StockBacktestPortfolio() for name in ("equal_weight", "technical", "fundamental", "lightgbm")}
    costs = (fee_bps + slippage_bps) / 10_000.0
    first_day = test_dates[test_indices.start]
    pending: dict[str, list[str]] = {}
    decisions = []
    executions = []
    curve = []
    for index in range(test_indices.start, len(test_dates)):
        day = test_dates[index]
        fills = {}
        for name, account in runs.items():
            account.mark(day, prices, delistings)
            if pending:
                turnover, fee, unfilled = account.rebalance(pending[name], day, prices, delistings, costs)
                fills[name] = {"turnover": turnover, "cost_index": fee, "unfilled_due_to_delisting": unfilled}
            account.settle_delistings(day)
        if fills:
            executions.append({"date": day.isoformat(), "strategies": fills})
        pending = {}

        if index in test_indices:
            snapshot, ranked = cohort(day)
            model_rows = _model_rows(ranked)
            scores = model.predict(_matrix(model_rows), num_iteration=iteration, num_threads=2) if model_rows else []
            lightgbm_selected, lightgbm_tie = _select_unambiguous_top(model_rows, scores, top)
            technical = rank_factor_rows(
                [{**row, "factor_percentiles": {}} for row in ranked], use_fundamentals=False,
            )
            technical_selected, technical_tie = _select_unambiguous_top(
                technical, [row["score"] for row in technical], top,
            )
            fundamental_selected, fundamental_tie = _select_unambiguous_top(
                ranked, [row["score"] for row in ranked], top,
            )
            pending = {
                "equal_weight": sorted(row["symbol"] for row in ranked),
                "technical": technical_selected,
                "fundamental": fundamental_selected,
                "lightgbm": lightgbm_selected,
            }
            decisions.append({"date": day.isoformat(), "execution_date": test_dates[index + 1].isoformat(),
                              "snapshot_as_of": snapshot.isoformat(),
                              "boundary_ties": {"lightgbm": lightgbm_tie, "technical": technical_tie,
                                                "fundamental": fundamental_tie},
                              "selected": pending})
        curve.append({"date": day.isoformat(), "benchmark": benchmark[day] / benchmark[first_day],
                      **{name: account.equity for name, account in runs.items()}})

    if input_hashes != _input_hashes(paths):
        raise ValueError("A study input changed while the comparison was running")

    return {
        "method": "matched_stock_selection_comparison_v1",
        "inputs_sha256": input_hashes,
        "windows": {"train_start": train_start, "train_end": train_end, "validation_end": validation_end,
                    "test_start": test_start, "test_end": test_end, "first_test_decision": first_day.isoformat(),
                    "last_test_close": test_dates[-1].isoformat()},
        "parameters": {"benchmark_symbol": ticker, "top": top, "rebalance_sessions": rebalance_days,
                       "fee_bps": fee_bps, "slippage_bps": slippage_bps, "num_boost_round": num_boost_round,
                       "min_train_rows": min_train_rows, "max_universe_age_days": max_universe_age_days,
                       "max_fundamental_age_days": max_fundamental_age_days},
        "model": {
            "lightgbm_version": version("lightgbm"),
            "features": list(COMPARISON_FEATURES), "best_iteration": iteration, "parameters": parameters,
            "model_sha256": sha256(model.model_to_string(num_iteration=iteration).encode()).hexdigest(),
            "row_counts": {split: len(rows) for split, rows in fit_data.items()},
            "decision_counts": {split: len(query[2]) for split, query in queries.items()},
            "last_label_exit": {split: max(row["label_exit_date"] for row in rows) for split, rows in fit_data.items()},
            "incomplete_cohorts": incomplete_cohorts, "purged_boundary_cohorts": purged_cohorts,
        },
        "strategies": {name: {**_performance(curve, name), "sum_turnover_ratios": account.turnover,
                              "total_cost_index": account.cost, "delisting_settlements": account.settlements}
                       for name, account in runs.items()},
        "benchmark": _performance(curve, "benchmark"),
        "decisions": decisions, "executions": executions, "equity_curve": curve,
        "live_execution_enabled": False,
    }
