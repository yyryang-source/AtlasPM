"""Forensic, no-model diagnostic for extreme 12-1 momentum Top-N outcomes.

Rebuilds precisely the V2 labeled cohorts but never trains, filters based on
future labels, trades, or presents this exploratory analysis as validated alpha.
All outputs are research diagnostics for investigating vendor/symbol outliers.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import isfinite
from pathlib import Path
from statistics import fmean, median
from typing import Any

from atlaspm.research.ai_stock_ranker import create_training_rows, chronological_splits
from atlaspm.research.ai_stock_ranker_v2 import _rank_scores
from atlaspm.data.historical import load_adjusted_prices


def _review_momentum_cohorts(
    rows: list[dict[str, Any]],
    benchmark: dict[date, float],
    *,
    top: int,
) -> dict[str, Any]:
    if not 1 <= top <= 100:
        raise ValueError("top must be 1..100")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["date"]].append(row)

    cohorts: list[dict[str, Any]] = []
    for decision, group in sorted(groups.items()):
        scores = [float(r["features"][0]) for r in group]
        selected, diagnostics = _rank_scores(group, scores, top=top)
        ambiguous = diagnostics["ties_cross_top_n_cutoff"]
        if ambiguous:
            # Arbitrarily breaking a tie would invalidate claimed Top-N alpha.
            cohorts.append({
                "decision_date": decision,
                "cohort_size": len(group),
                "top_n_boundary_tied": True,
                "top_n_outcome": None,
                "selected": [],
            })
            continue
        by_symbol = {r["symbol"]: r for r in group}
        chosen = [by_symbol[r["symbol"]] for r in selected]
        labels = [float(r["forward_excess_return"]) for r in chosen]
        pool_average = fmean(float(r["forward_excess_return"]) for r in group)
        entries = {r["entry_date"] for r in chosen}
        exits = {r["label_exit_date"] for r in chosen}
        if len(entries) != 1 or len(exits) != 1:
            raise ValueError(f"Multiple entry/exit labels within cohort {decision}")
        entry = date.fromisoformat(next(iter(entries)))
        exit_day = date.fromisoformat(next(iter(exits)))
        if entry not in benchmark or exit_day not in benchmark:
            raise ValueError(f"Missing SPY benchmark entry/exit for cohort {decision}")
        spy_growth = benchmark[exit_day] / benchmark[entry] - 1.0
        if not isfinite(spy_growth):
            raise ValueError("Non-finite benchmark return")
        selection = [{
            "symbol": r["symbol"],
            "momentum_12_1": float(r["features"][0]),
            "forward_stock_return": float(r["forward_excess_return"]) + spy_growth,
            "forward_spy_excess_return": float(r["forward_excess_return"]),
            "flag_extreme_momentum_gt_300pct": float(r["features"][0]) > 3.0,
            "flag_forward_stock_loss_gt_50pct": (
                float(r["forward_excess_return"]) + spy_growth < -0.5
            ),
        } for r in chosen]
        selected_worst = sorted(
            selection, key=lambda item: item["forward_spy_excess_return"]
        )
        cohorts.append({
            "decision_date": decision,
            "entry_date": entry.isoformat(),
            "label_exit_date": exit_day.isoformat(),
            "cohort_size": len(group),
            "top_n_boundary_tied": False,
            "top_n_outcome": {
                "average_forward_spy_excess": fmean(labels),
                "average_forward_stock_return": fmean(
                    item["forward_stock_return"] for item in selection
                ),
                "median_forward_spy_excess": median(labels),
                "average_same_pool_spy_excess": pool_average,
                "difference_vs_pool_mean": fmean(labels) - pool_average,
                "spy_return_entry_to_exit": spy_growth,
                "top_n_min_12_1_momentum": min(
                    item["momentum_12_1"] for item in selection
                ),
                "top_n_max_12_1_momentum": max(
                    item["momentum_12_1"] for item in selection
                ),
                "top_n_return_below_minus_50pct_count": sum(
                    item["flag_forward_stock_loss_gt_50pct"] for item in selection
                ),
                "top_n_momentum_over_300pct_count": sum(
                    item["flag_extreme_momentum_gt_300pct"] for item in selection
                ),
            },
            "selected": sorted(
                selection, key=lambda item: (-item["momentum_12_1"], item["symbol"])
            ),
            "worst_five_selected": selected_worst[:5],
        })
    valid = [c for c in cohorts if c["top_n_outcome"] is not None]
    return {
        "decision_dates": len(cohorts),
        "unambiguous_decision_dates": len(valid),
        "ambiguous_decision_dates": len(cohorts) - len(valid),
        "mean_top_n_forward_spy_excess": (
            fmean(c["top_n_outcome"]["average_forward_spy_excess"] for c in valid)
            if valid else None
        ),
        "mean_top_n_minus_pool_mean": (
            fmean(c["top_n_outcome"]["difference_vs_pool_mean"] for c in valid)
            if valid else None
        ),
        "total_extreme_momentum_picks": sum(
            c["top_n_outcome"]["top_n_momentum_over_300pct_count"] for c in valid
        ),
        "total_forward_losses_over_50pct": sum(
            c["top_n_outcome"]["top_n_return_below_minus_50pct_count"] for c in valid
        ),
        "cohorts": cohorts,
    }


def diagnose_v2_momentum_extremes(
    prices_path: Path,
    benchmark_path: Path,
    *,
    start: str,
    train_end: str,
    validation_end: str,
    test_end: str,
    horizon_sessions: int = 21,
    sample_every_sessions: int = 21,
    top: int = 10,
    benchmark_symbol: str = "SPY",
) -> dict[str, Any]:
    if not 1 <= top <= 100:
        raise ValueError("top must be 1..100")
    rows, dataset = create_training_rows(
        prices_path, benchmark_path, start=start, test_end=test_end,
        benchmark_symbol=benchmark_symbol,
        sample_every_sessions=sample_every_sessions,
        horizon_sessions=horizon_sessions,
    )
    splits = chronological_splits(
        rows, train_end=train_end,
        validation_end=validation_end, test_end=test_end,
    )
    benchmark = load_adjusted_prices(benchmark_path)[benchmark_symbol.strip().upper()]
    reports = {
        key: _review_momentum_cohorts(splits[key], benchmark, top=top)
        for key in ("validation", "test")
    }
    return {
        "status": "RESEARCH_DIAGNOSTIC_ONLY",
        "research_only": True,
        "live_execution_enabled": False,
        "diagnostic_goal": "Explain why high cross-sectional momentum IC coexists with poor extreme Top-N returns.",
        "dataset": dataset,
        "split_counts": {key: len(value) for key, value in splits.items()},
        "params": {
            "start": start, "train_end": train_end,
            "validation_end": validation_end, "test_end": test_end,
            "horizon_sessions": horizon_sessions,
            "sample_every_sessions": sample_every_sessions, "top": top,
            "benchmark_symbol": benchmark_symbol,
        },
        "validation": reports["validation"],
        "test": reports["test"],
        "warnings": [
            "Current US-listed snapshot is NOT historical point-in-time membership.",
            "Yahoo back-adjusted prices are unofficial and corporate-action-dependent.",
            "Suspicious extreme momentum/returns are FLAGS, not proven bad data.",
            "All values are historical diagnostic summaries and not costs, order fills, or compounded portfolio returns.",
            "2026 validation/test periods have already been inspected; this is exploratory and cannot validate fresh alpha.",
        ],
    }
