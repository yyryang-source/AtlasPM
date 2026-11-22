"""AtlasPM AI Stock Ranker: cross-sectional forward-excess-return prediction.

Research architecture inspired by public Microsoft Qlib stock ranking and
LightGBM benchmarks; original AtlasPM implementation, NOT pretrained Qlib,
Goldman Sachs or proprietary hedge fund model. No order sizing or execution.

Every feature is derived from closes on or before a completed decision date.
Training target begins at the NEXT benchmark-session close, ending H sessions
after entry; purged chronological splits prohibit labels crossing boundaries.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date
import hashlib
import json
from math import isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from atlaspm.historical import load_adjusted_prices


FEATURE_NAMES = (
    "momentum_12_1", "momentum_6_1", "momentum_3_1",
    "momentum_1m", "vs_sma200", "vs_sma50",
    "volatility_63", "volatility_21", "drawdown_252",
)
SCHEMA = "atlas_ai_stock_alpha_v1"
_RISK_DISCLOSURES = [
    "Predicted forward excess return is a model score, not a probability, guarantee or order.",
    "Today's surviving universe omits delisted/historical members: strong survivorship bias.",
    "Back-adjusted vendor prices may contain corporate-action revision leakage.",
    "This is NOT Goldman Sachs/JPMorgan proprietary alpha or a pretrained Qlib model.",
    "Validation labels tune model selection; reported historical test period was already inspected in prior AtlasPM work.",
    "Features are price-only; verified point-in-time financial statements and liquidity remain future work.",
    "No execution, position sizing, leverage, broker connections or investment advice.",
]


def _history_arrays(prices: dict[str, dict[date, float]]) -> dict[str, tuple[list[date], list[float]]]:
    result = {}
    for symbol, observations in prices.items():
        ordered = sorted(observations.items())
        result[symbol] = ([d for d, _ in ordered], [p for _, p in ordered])
    return result


def _features(
    history: tuple[list[date], list[float]], cutoff: date,
) -> list[float] | None:
    days, prices = history
    end = bisect_right(days, cutoff)
    if end < 253 or days[end - 1] != cutoff:
        return None  # No stale shares or forward-filling, 253 historical closes.
    tail = prices[end - 253:end]
    observed_days = days[end - 253:end]
    if ((observed_days[-1] - observed_days[0]).days > 420
            or any((b - a).days > 14 for a, b in zip(observed_days, observed_days[1:]))):
        return None
    one_month = tail[-1] / tail[-22] - 1
    returns_63 = [b / a - 1 for a, b in zip(tail[-64:-1], tail[-63:])]
    returns_21 = returns_63[-21:]
    high = tail[0]
    dd = 0.0
    for price in tail[1:]:
        high = max(high, price)
        dd = min(dd, price / high - 1)
    factors = [
        tail[-22] / tail[-253] - 1,
        tail[-22] / tail[-127] - 1,
        tail[-22] / tail[-64] - 1,
        one_month,
        tail[-1] / fmean(tail[-200:]) - 1,
        tail[-1] / fmean(tail[-50:]) - 1,
        stdev(returns_63) * sqrt(252),
        stdev(returns_21) * sqrt(252),
        dd,
    ]
    return factors if all(isfinite(x) for x in factors) else None


def _candidate_rows(
    history: dict[str, tuple[list[date], list[float]]],
    benchmark_days: list[date],
    benchmark: dict[date, float],
    decision_index: int,
    *,
    horizon: int | None,
) -> list[dict[str, Any]]:
    decision = benchmark_days[decision_index]
    if horizon is not None and decision_index + 1 + horizon >= len(benchmark_days):
        return []
    entry_index = decision_index + 1
    exit_index = entry_index + horizon if horizon is not None else None
    entry = benchmark_days[entry_index] if horizon is not None else None
    exit_day = benchmark_days[exit_index] if exit_index is not None else None
    bench_growth = (benchmark[exit_day] / benchmark[entry] - 1
                    if entry is not None and exit_day is not None else None)
    result = []
    for symbol, record in history.items():
        factors = _features(record, decision)
        if factors is None:
            continue
        label = None
        if horizon is not None:
            days, prices = record
            at_entry = bisect_left(days, entry)
            at_exit = bisect_left(days, exit_day)
            if (at_entry >= len(days) or days[at_entry] != entry
                    or at_exit >= len(days) or days[at_exit] != exit_day):
                continue  # No backfill or invented future execution.
            label = prices[at_exit] / prices[at_entry] - 1 - bench_growth
        result.append({
            "date": decision.isoformat(), "symbol": symbol,
            "features": factors,
            "forward_excess_return": label,
            "entry_date": entry.isoformat() if entry else None,
            "label_exit_date": exit_day.isoformat() if exit_day else None,
        })
    return result


def create_training_rows(
    prices_path: Path,
    benchmark_path: Path,
    *,
    start: str, test_end: str,
    benchmark_symbol: str = "SPY",
    sample_every_sessions: int = 21,
    horizon_sessions: int = 21,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Returns only labeled rows from completed close dates, no partial labels."""
    if (isinstance(sample_every_sessions, bool) or not 1 <= sample_every_sessions <= 252
            or isinstance(horizon_sessions, bool) or not 1 <= horizon_sessions <= 252):
        raise ValueError("sample-every-sessions and horizon-sessions must be 1..252")
    start_date, stop_date = date.fromisoformat(start), date.fromisoformat(test_end)
    if start_date >= stop_date:
        raise ValueError("Training start must be earlier than test end")
    prices = load_adjusted_prices(prices_path)
    benchmark_all = load_adjusted_prices(benchmark_path)
    benchmark_symbol = benchmark_symbol.strip().upper()
    if benchmark_symbol not in benchmark_all:
        raise ValueError(f"Missing {benchmark_symbol} benchmark history")
    prices.pop(benchmark_symbol, None)
    if len(prices) < 3:
        raise ValueError("Need 3+ non-benchmark stock symbols")
    bench = benchmark_all[benchmark_symbol]
    benchmark_days = sorted(d for d in bench if d <= stop_date)
    start_index = max(252, bisect_left(benchmark_days, start_date))
    end_index = len(benchmark_days) - 2 - horizon_sessions
    if start_index > end_index:
        raise ValueError("Not enough benchmark sessions for warmup and labels")
    histories = _history_arrays(prices)
    rows = []
    cohorts = 0
    for i in range(start_index, end_index + 1, sample_every_sessions):
        batch = _candidate_rows(
            histories, benchmark_days, bench, i, horizon=horizon_sessions
        )
        rows.extend(batch)
        cohorts += 1
    if not rows:
        raise ValueError("No 253-session stocks with valid future label prices")
    return rows, {
        "source_stock_universe": len(prices),
        "benchmark_symbol": benchmark_symbol,
        "start_requested": start,
        "test_end_requested": test_end,
        "first_sample_decision": rows[0]["date"],
        "last_sample_decision": rows[-1]["date"],
        "samples": len(rows),
        "sampled_decision_dates": cohorts,
        "horizon_sessions": horizon_sessions,
        "sample_every_sessions": sample_every_sessions,
        "feature_schema": SCHEMA,
        "feature_names": list(FEATURE_NAMES),
    }


def chronological_splits(
    rows: list[dict[str, Any]],
    *,
    train_end: str,
    validation_end: str,
    test_end: str,
) -> dict[str, list[dict[str, Any]]]:
    """A row enters a split only if its *label exit* lies inside the split."""
    a, b, c = date.fromisoformat(train_end), date.fromisoformat(validation_end), date.fromisoformat(test_end)
    if not a < b < c:
        raise ValueError("Require train-end < validation-end < test-end")
    output: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    for row in rows:
        decision = date.fromisoformat(row["date"])
        exit_day = date.fromisoformat(row["label_exit_date"])
        if decision <= a and exit_day <= a:
            output["train"].append(row)
        elif a < decision <= b and exit_day <= b:
            output["validation"].append(row)
        elif b < decision <= c and exit_day <= c:
            output["test"].append(row)
        # Purge: a label that crosses a calendar split does not belong to either.
    return output


def _midrank(values: list[float]) -> list[float]:
    result = [0.0] * len(values)
    ordered = sorted(range(len(values)), key=lambda i: (values[i], i))
    i = 0
    while i < len(values):
        j = i + 1
        while j < len(values) and values[ordered[j]] == values[ordered[i]]:
            j += 1
        rank = (i + j - 1) / 2 + 1
        for p in range(i, j):
            result[ordered[p]] = rank
        i = j
    return result


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    xx, yy = _midrank(xs), _midrank(ys)
    mx, my = fmean(xx), fmean(yy)
    numerator = sum((a - mx) * (b - my) for a, b in zip(xx, yy))
    dx = sum((a - mx) ** 2 for a in xx)
    dy = sum((b - my) ** 2 for b in yy)
    if dx < 1e-12 or dy < 1e-12:
        return None
    return numerator / sqrt(dx * dy)


def evaluate_ranker(
    rows: list[dict[str, Any]],
    predictions: list[float],
    *,
    top: int = 10,
) -> dict[str, Any]:
    if len(rows) != len(predictions):
        raise ValueError("Prediction length mismatch")
    grouped: dict[str, list[tuple[dict[str, Any], float]]] = defaultdict(list)
    for row, pred in zip(rows, predictions):
        if not isfinite(float(pred)):
            raise ValueError("Non-finite model prediction")
        grouped[row["date"]].append((row, float(pred)))
    ics: list[float] = []
    baseline_ics: list[float] = []
    top_spreads: list[float] = []
    top_excess: list[float] = []
    for cohort in grouped.values():
        targets = [float(row["forward_excess_return"]) for row, _ in cohort]
        predicted = [p for _, p in cohort]
        baseline = [float(row["features"][0]) for row, _ in cohort]
        corr = _spearman(predicted, targets)
        simple = _spearman(baseline, targets)
        if corr is not None:
            ics.append(corr)
        if simple is not None:
            baseline_ics.append(simple)
        if len(cohort) >= 3:
            winners = sorted(cohort, key=lambda t: (-t[1], t[0]["symbol"]))[:top]
            top_value = fmean(float(row["forward_excess_return"]) for row, _ in winners)
            top_excess.append(top_value)
            top_spreads.append(top_value - fmean(targets))
    return {
        "labeled_rows": len(rows),
        "decision_dates": len(grouped),
        "ic_decision_dates": len(ics),
        "mean_spearman_ic": fmean(ics) if ics else None,
        "simple_momentum_mean_spearman_ic": fmean(baseline_ics) if baseline_ics else None,
        "top_n": top,
        "mean_top_n_forward_excess_return": fmean(top_excess) if top_excess else None,
        "mean_top_n_minus_same_universe_mean": fmean(top_spreads) if top_spreads else None,
        "label_definition": "P[decision+1+h]/P[decision+1]-1 minus SPY same close dates",
        "note": "Per-cohort forward return averages, NOT tradable or compounded strategy performance.",
    }


def _load_lightgbm() -> Any:
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise ValueError("LightGBM not installed; run python -m pip install -e '.[ml]'") from exc
    return lgb


def _matrix(rows: list[dict[str, Any]]) -> Any:
    # LightGBM 4.x/4.7+ needs a NumPy ndarray, not list[list[float]].
    # NumPy is a transitive dependency of optional lightgbm [ml].
    import numpy as np
    return np.asarray([row["features"] for row in rows], dtype=np.float64)


def train_ai_stock_ranker(
    prices_path: Path, benchmark_path: Path, model_path: Path,
    *,
    start: str = "2000-01-01",
    train_end: str = "2018-12-31",
    validation_end: str = "2022-12-30",
    test_end: str = "2026-11-20",
    benchmark_symbol: str = "SPY",
    sample_every_sessions: int = 21,
    horizon_sessions: int = 21,
    num_boost_round: int = 180,
    min_train_rows: int = 300,
    top: int = 10,
) -> dict[str, Any]:
    if not 10 <= num_boost_round <= 2000:
        raise ValueError("num-boost-round must be 10..2000")
    if not 30 <= min_train_rows <= 1000000:
        raise ValueError("min-train-rows must be 30..1000000")
    if not 1 <= top <= 1000:
        raise ValueError("top must be 1..1000")
    if date.fromisoformat(start) >= date.fromisoformat(train_end):
        raise ValueError("start must precede train-end")
    rows, overview = create_training_rows(
        prices_path, benchmark_path, start=start, test_end=test_end,
        benchmark_symbol=benchmark_symbol,
        sample_every_sessions=sample_every_sessions,
        horizon_sessions=horizon_sessions,
    )
    split = chronological_splits(
        rows, train_end=train_end,
        validation_end=validation_end, test_end=test_end,
    )
    if len(split["train"]) < min_train_rows:
        raise ValueError(f"Insufficient training rows: {len(split['train'])} < {min_train_rows}")
    if len(split["validation"]) < 30 or len(split["test"]) < 30:
        raise ValueError("Need at least 30 labeled rows in validation and test each")

    lgb = _load_lightgbm()
    train = lgb.Dataset(
        _matrix(split["train"]),
        label=[float(r["forward_excess_return"]) for r in split["train"]],
        feature_name=list(FEATURE_NAMES),
    )
    validation = lgb.Dataset(
        _matrix(split["validation"]),
        label=[float(r["forward_excess_return"]) for r in split["validation"]],
        reference=train, feature_name=list(FEATURE_NAMES),
    )
    params = {
        "objective": "regression", "metric": "l2",
        "learning_rate": 0.04, "num_leaves": 15, "max_depth": 5,
        "min_data_in_leaf": 30, "feature_fraction": 0.85,
        "bagging_fraction": 0.90, "bagging_freq": 1,
        "verbosity": -1, "seed": 17, "num_threads": 2,
    }
    booster = lgb.train(
        params, train, num_boost_round=num_boost_round,
        valid_sets=[validation],
        callbacks=[lgb.early_stopping(stopping_rounds=25, verbose=False)],
    )
    best_iteration = booster.best_iteration or num_boost_round
    val_pred = booster.predict(_matrix(split["validation"]), num_iteration=best_iteration)
    test_pred = booster.predict(_matrix(split["test"]), num_iteration=best_iteration)
    val_metrics = evaluate_ranker(split["validation"], list(val_pred), top=top)
    test_metrics = evaluate_ranker(split["test"], list(test_pred), top=top)

    model_path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(model_path), num_iteration=best_iteration)
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    metadata = {
        "schema": SCHEMA,
        "model_kind": "LightGBM regression on cross-sectional next-close excess returns",
        "benchmark_symbol": overview["benchmark_symbol"],
        "model_sha256": digest,
        "feature_names": list(FEATURE_NAMES),
        "feature_count": len(FEATURE_NAMES),
        "model_last_knowledge_cutoff": validation_end,
        "train_end": train_end,
        "validation_end": validation_end,
        "test_end": test_end,
        "start": start,
        "forecast_horizon_sessions": horizon_sessions,
        "training_sample_interval_sessions": sample_every_sessions,
        "best_iteration": best_iteration,
        "hyperparameters": params,
        "live_execution_enabled": False,
    }
    metadata_path = model_path.with_suffix(model_path.suffix + ".meta.json")
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "trained_locally",
        "model_path": str(model_path),
        "metadata_path": str(metadata_path),
        "dataset": overview,
        "split_counts": {name: len(values) for name, values in split.items()},
        "purged_rows": len(rows) - sum(len(v) for v in split.values()),
        "training": {
            "model": "LightGBM regression baseline",
            "num_boost_round_requested": num_boost_round,
            "best_iteration": best_iteration,
        },
        "validation": val_metrics,
        "test": test_metrics,
        "model_sha256": digest,
        "live_execution_enabled": False,
        "warnings": list(_RISK_DISCLOSURES),
    }


def rank_with_ai(
    prices_path: Path, benchmark_path: Path, model_path: Path,
    *,
    as_of: str,
    top: int = 20,
    benchmark_symbol: str = "SPY",
) -> dict[str, Any]:
    if not 1 <= top <= 1000:
        raise ValueError("top must be 1..1000")
    cutoff = date.fromisoformat(as_of)
    meta_path = model_path.with_suffix(model_path.suffix + ".meta.json")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    if metadata.get("schema") != SCHEMA or metadata.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError("Model feature schema mismatch")
    if cutoff <= date.fromisoformat(metadata["model_last_knowledge_cutoff"]):
        raise ValueError("Requested date is not after model training/validation knowledge cutoff")
    if hashlib.sha256(model_path.read_bytes()).hexdigest() != metadata["model_sha256"]:
        raise ValueError("Model/metadata SHA256 mismatch")
    benchmark_all = load_adjusted_prices(benchmark_path)
    ticker = benchmark_symbol.strip().upper()
    if ticker != metadata.get("benchmark_symbol"):
        raise ValueError("Inference benchmark differs from training benchmark")
    if ticker not in benchmark_all:
        raise ValueError(f"Benchmark {ticker} missing")
    bench = benchmark_all[ticker]
    bench_days = sorted(d for d in bench if d <= cutoff)
    if not bench_days or bench_days[-1] != cutoff:
        raise ValueError("as-of must be a completed benchmark trading close")
    prices = load_adjusted_prices(prices_path)
    prices.pop(ticker, None)
    history = _history_arrays(prices)
    rows = _candidate_rows(
        history, bench_days, bench,
        len(bench_days) - 1, horizon=None,
    )
    if not rows:
        raise ValueError("No symbols with valid 253-session decision-close features")
    model = _load_lightgbm().Booster(model_file=str(model_path))
    prediction = model.predict(
        _matrix(rows), num_iteration=metadata.get("best_iteration")
    )
    ranked = sorted(
        [{
            "symbol": row["symbol"],
            "predicted_forward_excess_return": float(pred),
            "predicted_forward_excess_percent": round(float(pred) * 100, 4),
        } for row, pred in zip(rows, prediction)],
        key=lambda r: (-r["predicted_forward_excess_return"], r["symbol"]),
    )
    if any(not isfinite(x["predicted_forward_excess_return"]) for x in ranked):
        raise ValueError("Non-finite AI prediction")
    for idx, row in enumerate(ranked, 1):
        row["rank"] = idx
    return {
        "as_of": as_of,
        "model_kind": metadata["model_kind"],
        "forecast_horizon_sessions": metadata["forecast_horizon_sessions"],
        "model_last_knowledge_cutoff": metadata["model_last_knowledge_cutoff"],
        "universe_count": len(history),
        "scored_count": len(ranked),
        "excluded_count": len(history) - len(ranked),
        "selected": ranked[:top],
        "feature_names": list(FEATURE_NAMES),
        "model_sha256": metadata["model_sha256"],
        "live_execution_enabled": False,
        "warnings": list(_RISK_DISCLOSURES),
    }
