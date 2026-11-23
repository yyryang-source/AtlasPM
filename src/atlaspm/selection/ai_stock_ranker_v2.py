"""AtlasPM cross-sectional stock learning-to-rank V2 (no trades, no risk stack).

Independent original LightGBM rank_xendcg model trained on groups of
securities observed on the SAME historical decision close. Uses strictly
dated forward-return labels and purged chronology from V1.

Raw ranking scores are **not** return forecasts or calibrated probabilities.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
import json
from math import floor, isfinite
from pathlib import Path
from statistics import fmean
from typing import Any

from atlaspm.historical import load_adjusted_prices
from atlaspm.ai_stock_ranker import (
    FEATURE_NAMES, _RISK_DISCLOSURES, _candidate_rows, _history_arrays,
    _load_lightgbm, _matrix, _midrank, chronological_splits,
    create_training_rows, evaluate_ranker,
)

SCHEMA = "atlas_ai_stock_learning_to_rank_v2"
LEVELS = 5
_SCORE_DIGITS = 7


def _rank_queries(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[int], list[int]]:
    """Sort by cohort and compute historical within-date 0..4 relevance.

    Ranking labels are only available in training/validation/test cohorts,
    never at inference. Future performance for each decision is evaluated
    *within that one date*, not across different market regimes.
    """
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["forward_excess_return"] is None:
            raise ValueError("Learning-to-rank requires fully matured future labels")
        by_date[row["date"]].append(row)
    ordered: list[dict[str, Any]] = []
    labels: list[int] = []
    groups: list[int] = []
    for d in sorted(by_date):
        group = sorted(by_date[d], key=lambda r: r["symbol"])
        if len(group) < 3:
            continue
        returns = [float(r["forward_excess_return"]) for r in group]
        if not all(isfinite(x) for x in returns):
            raise ValueError("Non-finite future label")
        # Mid-ranks keep identical returns in the same relevance grade.
        ranks = _midrank(returns)
        levels = [
            min(LEVELS - 1, max(0, floor((rank - 1) / len(group) * LEVELS)))
            for rank in ranks
        ]
        ordered.extend(group)
        labels.extend(levels)
        groups.append(len(group))
    if sum(groups) != len(ordered) or sum(groups) != len(labels):
        raise ValueError("Broken ranking-group boundaries")
    if not groups:
        raise ValueError("No ranking groups with >=3 complete securities")
    return ordered, labels, groups


def _rank_scores(
    rows: list[dict[str, Any]], values: Any, top: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Truthful competition ranks: ties at visible score precision stay ties."""
    if len(rows) != len(values):
        raise ValueError("Prediction length mismatch")
    all_scores = []
    for row, raw in zip(rows, values):
        value = float(raw)
        if not isfinite(value):
            raise ValueError("Invalid rank model score")
        all_scores.append({
            "symbol": row["symbol"],
            "ranking_score": round(value, _SCORE_DIGITS),
        })
    all_scores.sort(key=lambda r: (-r["ranking_score"], r["symbol"]))
    n_groups, largest_tie, tied_stocks = 0, 0, 0
    pos = 0
    while pos < len(all_scores):
        end = pos + 1
        score = all_scores[pos]["ranking_score"]
        while end < len(all_scores) and all_scores[end]["ranking_score"] == score:
            end += 1
        size = end - pos
        n_groups += 1
        largest_tie = max(largest_tie, size)
        if size > 1:
            tied_stocks += size
        for index in range(pos, end):
            all_scores[index]["rank"] = pos + 1
            all_scores[index]["display_position"] = index + 1
            all_scores[index]["tie_count"] = size
        pos = end
    cutoff = min(top, len(all_scores))
    boundary_tie = bool(
        cutoff and all_scores[cutoff - 1]["rank"]
        + all_scores[cutoff - 1]["tie_count"] - 1 > cutoff
    )
    return all_scores[:top], {
        "scored_stocks": len(all_scores),
        "score_levels": n_groups,
        "fraction_unique_levels": n_groups / len(all_scores),
        "largest_tied_group": largest_tie,
        "stocks_with_tied_scores": tied_stocks,
        "ties_cross_top_n_cutoff": boundary_tie,
        "rank_quality": "REVIEW_REQUIRED" if boundary_tie else "TIES_REPORTED",
        "note": "Raw ranking score is an ordinal utility, NOT a percent return.",
    }


def _rank_evaluation(rows: list[dict[str, Any]], scores: Any, top: int) -> dict[str, Any]:
    """Evaluate ordinal model, but never treat arbitrary Top-N tie breaks as alpha.

    The shared generic evaluate_ranker() reports every date's Top N after
    falling back to symbol-order tie breaks. Preserve those historic figures
    ONLY as explicitly labeled legacy diagnostics. V2's headline Top N
    metrics include exclusively dates with an unambiguous selection boundary.
    Also evaluate the raw momentum factor 0 on the same dates.
    """
    values = list(scores)
    evaluated = evaluate_ranker(rows, values, top=top)
    cohorts: dict[str, list[tuple[dict[str, Any], float]]] = defaultdict(list)
    for row, pred in zip(rows, values):
        cohorts[row["date"]].append((row, float(pred)))

    fractions: list[float] = []
    cutoff_ties = 0
    model_excess: list[float] = []
    model_spread: list[float] = []
    momentum_excess: list[float] = []
    momentum_spread: list[float] = []
    momentum_ties = 0

    for cohort in cohorts.values():
        day_rows = [row for row, _ in cohort]
        predicted = [prediction for _, prediction in cohort]
        selected, diagnostic = _rank_scores(day_rows, predicted, top)
        fractions.append(diagnostic["fraction_unique_levels"])
        cutoff_ties += bool(diagnostic["ties_cross_top_n_cutoff"])

        realized = {row["symbol"]: float(row["forward_excess_return"]) for row in day_rows}
        population_mean = fmean(realized.values())
        if not diagnostic["ties_cross_top_n_cutoff"]:
            observed = fmean(realized[item["symbol"]] for item in selected)
            model_excess.append(observed)
            model_spread.append(observed - population_mean)

        momentum_selected, momentum_diagnostic = _rank_scores(
            day_rows, [float(row["features"][0]) for row in day_rows], top,
        )
        momentum_ties += bool(momentum_diagnostic["ties_cross_top_n_cutoff"])
        if not momentum_diagnostic["ties_cross_top_n_cutoff"]:
            benchmark_observed = fmean(
                realized[item["symbol"]] for item in momentum_selected
            )
            momentum_excess.append(benchmark_observed)
            momentum_spread.append(benchmark_observed - population_mean)

    # Explicitly relocate the old symbol-tie-broken Top N figures to a
    # non-actionable diagnostics field. Do NOT report them as a valid Top N.
    evaluated["legacy_symbol_tiebroken_top_n_diagnostic"] = {
        "mean_forward_excess_return": evaluated["mean_top_n_forward_excess_return"],
        "mean_minus_same_universe_mean": evaluated["mean_top_n_minus_same_universe_mean"],
        "not_a_valid_portfolio_result": True,
        "warning": (
            "Historical generic evaluation broke tied model scores by ticker "
            "alphabetically. These statistics are NOT selection alpha."
        ),
    }
    evaluated["mean_top_n_forward_excess_return"] = (
        fmean(model_excess) if model_excess else None
    )
    evaluated["mean_top_n_minus_same_universe_mean"] = (
        fmean(model_spread) if model_spread else None
    )
    evaluated["top_n_unambiguous_decision_dates"] = len(model_excess)
    evaluated["top_n_ambiguous_decision_dates"] = cutoff_ties
    evaluated["top_n_selection_quality"] = (
        "ALL_AMBIGUOUS" if not model_excess else
        "PARTIALLY_AMBIGUOUS" if cutoff_ties else
        "ALL_UNAMBIGUOUS"
    )
    evaluated["mean_fraction_unique_levels"] = fmean(fractions)
    evaluated["simple_momentum_top_n"] = {
        "unambiguous_decision_dates": len(momentum_excess),
        "ambiguous_decision_dates": momentum_ties,
        "mean_forward_excess_return": (
            fmean(momentum_excess) if momentum_excess else None
        ),
        "mean_minus_same_universe_mean": (
            fmean(momentum_spread) if momentum_spread else None
        ),
    }
    evaluated["interpretation"] = (
        "Only unambiguous Top-N cohort boundaries contribute to the headline "
        "return diagnostic; None means no evaluable Top N. Mean per-date "
        "forward SPY excess is not a compounded or costed tradable strategy. "
        "Simple momentum is an explicitly reported baseline."
    )
    return evaluated


def _model_research_review(
    validation: dict[str, Any],
    test: dict[str, Any],
) -> dict[str, Any]:
    """Research-only fail-closed diagnostics, not a broker/risk authorization."""
    reasons: list[str] = []
    for name, metrics in (("VALIDATION", validation), ("TEST", test)):
        # 12 decision dates is a minimum diagnostic sanity threshold, NOT
        # statistical proof of out-of-sample alpha.
        if metrics["decision_dates"] < 12:
            reasons.append(
                f"{name}_TOO_FEW_DECISION_DATES: {metrics['decision_dates']} < 12"
            )
        if metrics["top_n_ambiguous_decision_dates"]:
            reasons.append(
                f"{name}_TOP_N_BOUNDARY_TIES: "
                f"{metrics['top_n_ambiguous_decision_dates']} ambiguous dates"
            )
        if metrics["top_n_unambiguous_decision_dates"] == 0:
            reasons.append(f"{name}_NO_UNAMBIGUOUS_TOP_N_EVALUATION")
    test_ic = test["mean_spearman_ic"]
    baseline_ic = test["simple_momentum_mean_spearman_ic"]
    if test_ic is None or test_ic <= 0:
        reasons.append("TEST_IC_NOT_POSITIVE")
    if baseline_ic is not None and (test_ic is None or test_ic <= baseline_ic):
        reasons.append("TEST_IC_NOT_BETTER_THAN_SIMPLE_MOMENTUM")
    return {
        "status": "REVIEW_REQUIRED" if reasons else "PRELIMINARY_RESEARCH_REVIEW",
        "reasons": reasons,
        "no_trading_authorization": True,
        "note": (
            "Even PRELIMINARY_RESEARCH_REVIEW is not an alpha validation or "
            "automated trading approval. Requires historical PIT universe, "
            "more genuinely unseen decision periods and costed portfolio tests."
        ),
    }


def fit_rank_queries(
    train: tuple[list[dict[str, Any]], list[int], list[int]],
    validation: tuple[list[dict[str, Any]], list[int], list[int]],
    *,
    feature_names: tuple[str, ...],
    num_boost_round: int,
) -> tuple[Any, int, dict[str, Any]]:
    """Fit from labeled date groups and choose the iteration on validation only."""
    lgb = _load_lightgbm()
    train_rows, train_labels, train_groups = train
    val_rows, val_labels, val_groups = validation
    train_set = lgb.Dataset(
        _matrix(train_rows), label=train_labels, group=train_groups,
        feature_name=list(feature_names),
    )
    val_set = lgb.Dataset(
        _matrix(val_rows), label=val_labels, group=val_groups,
        reference=train_set, feature_name=list(feature_names),
    )
    params = {
        "objective": "rank_xendcg", "metric": "ndcg", "ndcg_eval_at": [5, 10],
        "learning_rate": 0.035, "num_leaves": 15, "max_depth": 5,
        "min_data_in_leaf": 20, "feature_fraction": 0.9,
        "bagging_fraction": 0.9, "bagging_freq": 1,
        "verbosity": -1, "seed": 17, "num_threads": 2,
        "label_gain": [0, 1, 3, 7, 15],
    }
    model = lgb.train(
        params, train_set, valid_sets=[val_set],
        num_boost_round=num_boost_round,
        callbacks=[lgb.early_stopping(stopping_rounds=35, verbose=False)],
    )
    iteration = model.best_iteration or num_boost_round
    return model, iteration, params


def train_stock_learning_to_rank(
    prices_path: Path,
    benchmark_path: Path,
    model_path: Path,
    *,
    start: str = "2000-01-01",
    train_end: str = "2018-12-31",
    validation_end: str = "2022-12-30",
    test_end: str = "2026-11-20",
    benchmark_symbol: str = "SPY",
    horizon_sessions: int = 21,
    sample_every_sessions: int = 21,
    num_boost_round: int = 300,
    top: int = 10,
    min_train_rows: int = 300,
) -> dict[str, Any]:
    if not 10 <= num_boost_round <= 2000:
        raise ValueError("num-boost-round must be 10..2000")
    if not 1 <= top <= 1000:
        raise ValueError("top must be in 1..1000")
    if not 30 <= min_train_rows <= 1000000:
        raise ValueError("min-train-rows must be in 30..1000000")
    if date.fromisoformat(start) >= date.fromisoformat(train_end):
        raise ValueError("start must precede train-end")
    rows, overview = create_training_rows(
        prices_path, benchmark_path, start=start, test_end=test_end,
        benchmark_symbol=benchmark_symbol,
        sample_every_sessions=sample_every_sessions,
        horizon_sessions=horizon_sessions,
    )
    split = chronological_splits(
        rows, train_end=train_end, validation_end=validation_end, test_end=test_end,
    )
    queries = {name: _rank_queries(s) for name, s in split.items()}
    for name in ("train", "validation", "test"):
        if len(queries[name][0]) < (min_train_rows if name == "train" else 30):
            raise ValueError(f"Insufficient {name} ranker observations")
    train_rows, _, train_groups = queries["train"]
    val_rows, _, val_groups = queries["validation"]
    test_rows, _, test_groups = queries["test"]
    model, iteration, params = fit_rank_queries(
        queries["train"], queries["validation"],
        feature_names=FEATURE_NAMES, num_boost_round=num_boost_round,
    )
    val_scores = model.predict(_matrix(val_rows), num_iteration=iteration)
    test_scores = model.predict(_matrix(test_rows), num_iteration=iteration)
    val_metrics = _rank_evaluation(val_rows, val_scores, top)
    test_metrics = _rank_evaluation(test_rows, test_scores, top)
    research_review = _model_research_review(val_metrics, test_metrics)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(model_path), num_iteration=iteration)
    sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
    metadata = {
        "schema": SCHEMA,
        "model_kind": "LightGBM rank_xendcg cross-sectional ranking",
        "score_type": "uncalibrated_relative_rank_utility_not_return",
        "model_sha256": sha, "best_iteration": iteration,
        "benchmark_symbol": overview["benchmark_symbol"],
        "feature_names": list(FEATURE_NAMES),
        "feature_schema": overview["feature_schema"],
        "label_levels": LEVELS,
        "start": start, "train_end": train_end,
        "validation_end": validation_end, "test_end": test_end,
        "model_last_knowledge_cutoff": validation_end,
        "horizon_sessions": horizon_sessions,
        "sample_every_sessions": sample_every_sessions,
        "hyperparameters": params,
        "live_execution_enabled": False,
    }
    meta_path = model_path.with_suffix(model_path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "trained_locally",
        "model_path": str(model_path),
        "metadata_path": str(meta_path),
        "model_sha256": sha,
        "method": "cross_sectional_rank_xendcg_v2",
        "dataset": overview,
        "split_counts": {name: len(query[0]) for name, query in queries.items()},
        "group_counts": {
            "train": len(train_groups),
            "validation": len(val_groups),
            "test": len(test_groups),
        },
        "purged_rows": len(rows) - sum(len(s) for s in split.values()),
        "training": {
            "best_iteration": iteration,
            "num_boost_round_requested": num_boost_round,
        },
        "validation": val_metrics,
        "test": test_metrics,
        "model_research_review": research_review,
        "live_execution_enabled": False,
        "warnings": list(_RISK_DISCLOSURES) + [
            "Ranking model score is not a forecast of percentage return.",
            "Compare test IC and tie frequency against regression V1; a less tied ranking is not automatically more accurate.",
            "Historical 2023-2026 test period was previously analyzed; future untouched data still needed.",
            "Naive Top-N means from ticker-alphabetical tie breaks are diagnostics only and never count as validated alpha.",
        ],
    }


def rank_stocks_v2(
    prices_path: Path, benchmark_path: Path, model_path: Path,
    *,
    as_of: str, top: int = 20, benchmark_symbol: str = "SPY",
) -> dict[str, Any]:
    if not 1 <= top <= 1000:
        raise ValueError("top must be 1..1000")
    cutoff = date.fromisoformat(as_of)
    meta_path = model_path.with_suffix(model_path.suffix + ".meta.json")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    if metadata.get("schema") != SCHEMA or metadata.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError("Ranker model feature schema mismatch")
    if benchmark_symbol.strip().upper() != metadata["benchmark_symbol"]:
        raise ValueError("Inference benchmark differs from training benchmark")
    if cutoff <= date.fromisoformat(metadata["model_last_knowledge_cutoff"]):
        raise ValueError("Inference date precedes model validation cutoff")
    if hashlib.sha256(model_path.read_bytes()).hexdigest() != metadata["model_sha256"]:
        raise ValueError("Model SHA256 does not match metadata")
    benchmark = load_adjusted_prices(benchmark_path)[metadata["benchmark_symbol"]]
    dates = sorted(d for d in benchmark if d <= cutoff)
    if not dates or dates[-1] != cutoff:
        raise ValueError("as-of must have a completed benchmark trading close")
    histories = load_adjusted_prices(prices_path)
    histories.pop(metadata["benchmark_symbol"], None)
    candidates = _candidate_rows(
        _history_arrays(histories), dates, benchmark, len(dates) - 1, horizon=None
    )
    if not candidates:
        raise ValueError("No stocks with complete decision-close history")
    model = _load_lightgbm().Booster(model_file=str(model_path))
    predictions = model.predict(_matrix(candidates), num_iteration=metadata["best_iteration"])
    selection, diagnostics = _rank_scores(candidates, predictions, top)
    importance = sorted((
        {
            "feature": feature, "gain": round(float(gain), 6),
            "splits": int(splits),
        }
        for feature, gain, splits in zip(
            FEATURE_NAMES, model.feature_importance("gain"), model.feature_importance("split")
        )
    ), key=lambda r: (-r["gain"], r["feature"]))
    return {
        "as_of": as_of,
        "method": "cross_sectional_rank_xendcg_v2",
        "score_definition": "Ordinal LightGBM rank utility; NOT predicted % return",
        "forecast_horizon_sessions": metadata["horizon_sessions"],
        "candidate_universe_count": len(histories),
        "scored_count": len(candidates),
        "excluded_count": len(histories) - len(candidates),
        "scored_symbols": sorted({r["symbol"] for r in candidates}),
        "selected": selection,
        "ranking_diagnostics": diagnostics,
        "model_diagnostics": {
            "best_iteration": metadata["best_iteration"],
            "number_of_trees_saved": model.num_trees(),
            "feature_importance": importance,
        },
        "model_sha256": metadata["model_sha256"],
        "live_execution_enabled": False,
        "warnings": list(_RISK_DISCLOSURES) + (
            ["Selected Top N cuts through an equally scored group."]
            if diagnostics["ties_cross_top_n_cutoff"] else []
        ),
    }
