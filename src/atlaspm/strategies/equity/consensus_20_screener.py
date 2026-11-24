"""AtlasPM 20-signal / five-family offline equity research consensus.

Original implementation informed by open-source screening and quant-research
project design ideas (see docs/selection/OPEN_SOURCE_SCREENER_20_REVIEW.md).
NO third-party code imported, copied or required; no order/broker/risk wiring.

IMPORTANT: 20 price signals are NOT 20 independently validated profitable
models. They share price data and are correlated. Data is a current-listing
snapshot, not a point-in-time historical universe. All outputs research only.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
import csv
from datetime import date
from math import isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev
from typing import Any


SIGNAL_FAMILIES: dict[str, tuple[str, ...]] = {
    "momentum": (
        "momentum_12_1", "momentum_6_1", "momentum_3_1", "momentum_63",
    ),
    "trend": (
        "price_vs_sma200", "price_vs_sma100", "price_vs_sma50",
        "sma50_vs_sma200", "near_52w_high",
    ),
    "defensive": (
        "low_volatility_63", "low_volatility_21",
        "low_downside_vol_63", "max_drawdown_252", "worst_day_63",
    ),
    "mean_reversion": (
        "pullback_5", "pullback_21", "dip_from_20d_high",
    ),
    "consistency": (
        "positive_days_63", "positive_days_126", "trend_efficiency_63",
    ),
}
FAMILY_WEIGHTS = {
    "momentum": 0.30, "trend": 0.25, "defensive": 0.20,
    "mean_reversion": 0.10, "consistency": 0.15,
}
SIGNALS = tuple(s for names in SIGNAL_FAMILIES.values() for s in names)
if len(SIGNALS) != 20 or len(set(SIGNALS)) != 20 or abs(sum(FAMILY_WEIGHTS.values()) - 1) > 1e-12:
    raise RuntimeError("Invalid 20-signal five-family research catalog")


def _percentiles(values: dict[str, float]) -> dict[str, float]:
    """Midrank tie-aware percentiles; deterministic and never by ticker."""
    if not values:
        return {}
    if any(not isfinite(v) for v in values.values()):
        raise ValueError("Nonfinite screening signal")
    ordered = sorted(values, key=lambda symbol: (values[symbol], symbol))
    if len(ordered) == 1:
        return {ordered[0]: 0.5}
    result: dict[str, float] = {}
    i = 0
    n = len(ordered)
    while i < n:
        j = i + 1
        while j < n and values[ordered[j]] == values[ordered[i]]:
            j += 1
        rank = (i + j - 1) / 2 / (n - 1)
        for symbol in ordered[i:j]:
            result[symbol] = rank
        i = j
    return result


def _signals(closes: list[float]) -> dict[str, float]:
    if len(closes) != 253 or any(p <= 0 or not isfinite(p) for p in closes):
        raise ValueError("Screening needs 253 finite positive adjusted closes")
    p = closes
    rs126 = [b / a - 1 for a, b in zip(p[-127:-1], p[-126:])]
    rs63 = rs126[-63:]
    rs21 = rs63[-21:]
    sma200, sma100, sma50 = (fmean(p[-length:]) for length in (200, 100, 50))
    high = p[0]
    dd = 0.0
    for close in p[1:]:
        high = max(high, close)
        dd = min(dd, close / high - 1)
    # Efficiency is signed: choppy or directionless paths score poorly.
    travel = sum(abs(b - a) for a, b in zip(p[-64:-1], p[-63:]))
    ef = (p[-1] - p[-64]) / travel if travel > 0 else 0.0
    features = {
        "momentum_12_1": p[-22] / p[-253] - 1,
        "momentum_6_1": p[-22] / p[-127] - 1,
        "momentum_3_1": p[-22] / p[-64] - 1,
        "momentum_63": p[-1] / p[-64] - 1,
        "price_vs_sma200": p[-1] / sma200 - 1,
        "price_vs_sma100": p[-1] / sma100 - 1,
        "price_vs_sma50": p[-1] / sma50 - 1,
        "sma50_vs_sma200": sma50 / sma200 - 1,
        "near_52w_high": p[-1] / high,
        "low_volatility_63": -stdev(rs63) * sqrt(252),
        "low_volatility_21": -stdev(rs21) * sqrt(252),
        "low_downside_vol_63": -sqrt(fmean(min(r, 0.0) ** 2 for r in rs63) * 252),
        "max_drawdown_252": dd,
        "worst_day_63": min(rs63),
        "pullback_5": 1 - p[-1] / p[-6],
        "pullback_21": 1 - p[-1] / p[-22],
        "dip_from_20d_high": 1 - p[-1] / max(p[-20:]),
        "positive_days_63": sum(r > 0 for r in rs63) / len(rs63),
        "positive_days_126": sum(r > 0 for r in rs126) / len(rs126),
        "trend_efficiency_63": ef,
    }
    if set(features) != set(SIGNALS) or not all(isfinite(v) for v in features.values()):
        raise ValueError("Invalid price-only signal family")
    return features


def _load_screening_history(
    prices_path: Path, cutoff: date, *,
    min_price: float, max_daily_ratio: float,
    symbols_allowed: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Single CSV pass, last 253 close observations per symbol, no future data."""
    histories: dict[str, deque[tuple[date, float]]] = defaultdict(lambda: deque(maxlen=253))
    last_seen: dict[str, date] = {}
    rows_read = 0
    rows_future_ignored = 0
    non_universe_rows = 0
    with prices_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or not {"date", "symbol", "adjusted_close"}.issubset(reader.fieldnames):
            raise ValueError("Price CSV needs date,symbol,adjusted_close")
        for line, row in enumerate(reader, start=2):
            rows_read += 1
            try:
                symbol = row["symbol"].strip().upper()
                d = date.fromisoformat(row["date"].strip())
                close = float(row["adjusted_close"])
                if not symbol or not isfinite(close) or close <= 0:
                    raise ValueError("invalid adjusted close or symbol")
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid price row {line}") from exc
            if d > cutoff:
                rows_future_ignored += 1
                continue
            if symbols_allowed is not None and symbol not in symbols_allowed:
                non_universe_rows += 1
                continue
            prev = last_seen.get(symbol)
            if prev is not None and d <= prev:
                raise ValueError(f"Duplicate or nonchronological history for {symbol} on {d}")
            last_seen[symbol] = d
            histories[symbol].append((d, close))

    excluded = Counter()
    anomaly_samples: list[dict[str, Any]] = []
    eligible: dict[str, dict[str, Any]] = {}
    valid_history_names = 0
    for symbol, history in sorted(histories.items()):
        recent = list(history)
        if len(recent) < 253:
            excluded["INSUFFICIENT_HISTORY"] += 1
            continue
        dates = [d for d, _ in recent]
        closes = [p for _, p in recent]
        if dates[-1] != cutoff:
            excluded["MISSING_EXACT_DECISION_CLOSE"] += 1
            continue
        if (dates[-1] - dates[0]).days > 420 or any(
            (b - a).days > 14 for a, b in zip(dates, dates[1:])
        ):
            excluded["SPARSE_OR_GAPPED_HISTORY"] += 1
            continue
        valid_history_names += 1
        spikes = [
            (dates[i].isoformat(), round(closes[i] / closes[i - 1], 6))
            for i in range(1, len(closes))
            if closes[i] / closes[i - 1] >= max_daily_ratio
            or closes[i] / closes[i - 1] <= 1 / max_daily_ratio
        ]
        if spikes:
            excluded["SUSPECT_CORPORATE_ACTION_OR_EXTREME_JUMP"] += 1
            if len(anomaly_samples) < 50:
                anomaly_samples.append({"symbol": symbol, "sample_date_ratio": spikes[:3]})
            continue
        if closes[-1] < min_price:
            excluded["BELOW_MIN_ADJUSTED_PRICE"] += 1
            continue
        try:
            features = _signals(closes)
        except (ValueError, OverflowError, ZeroDivisionError):
            excluded["INVALID_SIGNAL_FEATURES"] += 1
            continue
        eligible[symbol] = {
            "symbol": symbol,
            "as_of": cutoff.isoformat(),
            "adjusted_close": round(closes[-1], 6),
            "signals": features,
        }

    return eligible, {
        "price_rows_read": rows_read,
        "future_price_rows_ignored": rows_future_ignored,
        "rows_outside_supplied_current_universe": non_universe_rows,
        "unique_symbols_in_prices": len(histories),
        "valid_exact_close_history_names": valid_history_names,
        "eligible_price_only_names": len(eligible),
        "excluded_counts": dict(sorted(excluded.items())),
        "corporate_action_extreme_jump_samples": anomaly_samples,
    }


def run_20_signal_research(
    prices_path: Path, *, as_of: str, top: int = 10,
    min_price: float = 5.0, max_daily_ratio: float = 4.0,
    universe_path: Path | None = None,
    min_screenable: int = 100,
) -> dict[str, Any]:
    """All candidate scores use only exact-session trailing price history.

    Data provenance is deliberately limited: current listing universe,
    no historical point-in-time membership, no fundamental/volume verification.
    Scores are *relative percentiles*, never predicted percentage returns.
    """
    if isinstance(top, bool) or not 1 <= top <= 100:
        raise ValueError("top must be 1..100")
    if isinstance(min_screenable, bool) or min_screenable < 3:
        raise ValueError("min-screenable must be >= 3")
    if not isfinite(min_price) or min_price < 0:
        raise ValueError("min-price must be finite and >= 0")
    if not isfinite(max_daily_ratio) or max_daily_ratio <= 1:
        raise ValueError("max-daily-ratio must be finite and > 1")
    try:
        decision = date.fromisoformat(as_of)
        if decision.isoformat() != as_of:
            raise ValueError
    except ValueError as exc:
        raise ValueError("as-of must be YYYY-MM-DD") from exc

    symbols_allowed = None
    if universe_path is not None:
        from atlaspm.us_market_universe import read_market_universe
        manifest = read_market_universe(universe_path)
        symbols_allowed = {r["provider_symbol"].strip().upper() for r in manifest}
        if len(symbols_allowed) != len(manifest):
            raise ValueError("Duplicate symbols in supplied current universe")

    eligible, diagnostics = _load_screening_history(
        prices_path, decision, min_price=min_price,
        max_daily_ratio=max_daily_ratio, symbols_allowed=symbols_allowed,
    )
    if len(eligible) < 3:
        raise ValueError("Fewer than three screenable stocks with exact decision-close history")
    symbols = sorted(eligible)
    sig_percentiles = {}
    for signal in SIGNALS:
        sig_percentiles[signal] = _percentiles({
            symbol: eligible[symbol]["signals"][signal] for symbol in symbols
        })
    families_raw: dict[str, dict[str, float]] = {}
    for family, signals in SIGNAL_FAMILIES.items():
        families_raw[family] = {
            symbol: fmean(sig_percentiles[s][symbol] for s in signals)
            for symbol in symbols
        }
    family_votes = {
        family: _percentiles(families_raw[family])
        for family in SIGNAL_FAMILIES
    }
    results = []
    for symbol in symbols:
        votes = {
            family: round(100 * family_votes[family][symbol], 4)
            for family in SIGNAL_FAMILIES
        }
        score = round(100 * sum(
            FAMILY_WEIGHTS[family] * family_votes[family][symbol]
            for family in SIGNAL_FAMILIES
        ), 8)
        agreement = sum(family_votes[family][symbol] >= 0.8 for family in SIGNAL_FAMILIES)
        result = eligible[symbol]
        results.append({
            "symbol": symbol,
            "as_of": as_of,
            "adjusted_close": result["adjusted_close"],
            "consensus_score": score,
            "family_percentile_scores": votes,
            "family_top_quintile_agreement_count": agreement,
            "key_features": {
                "momentum_12_1": round(result["signals"]["momentum_12_1"], 6),
                "momentum_6_1": round(result["signals"]["momentum_6_1"], 6),
                "volatility_63_ann": round(-result["signals"]["low_volatility_63"], 6),
                "historical_max_drawdown": round(result["signals"]["max_drawdown_252"], 6),
                "last_close_vs_sma200": round(result["signals"]["price_vs_sma200"], 6),
            },
        })
    results.sort(key=lambda r: (-r["consensus_score"], r["symbol"]))
    # Avoid arbitrary alphabetical Top N when the cutoff score is tied.
    boundary_tied = (
        top < len(results)
        and results[top - 1]["consensus_score"] == results[top]["consensus_score"]
    )
    pos = 0
    while pos < len(results):
        end = pos + 1
        while end < len(results) and results[end]["consensus_score"] == results[pos]["consensus_score"]:
            end += 1
        for item in results[pos:end]:
            item["rank"] = pos + 1
            item["tie_count"] = end - pos
        pos = end
    reasons = [
        "SURVIVORSHIP_BIAS: current listings are not point-in-time historical memberships",
        "LIQUIDITY_UNVERIFIED: close-only CSV lacks trading volume/dollar volume",
        "CORPORATE_ACTIONS_NOT_EXHAUSTIVELY_VERIFIED: extreme daily jumps quarantined, not corrected",
        "FUNDAMENTALS_UNAVAILABLE: no publication-dated quality/value/growth inputs",
        "ALPHA_UNVALIDATED: 20 correlated signals are heuristics, not proven out-of-sample alpha",
    ]
    if len(eligible) < min_screenable:
        reasons.append(f"TOO_FEW_SCREENABLE_NAMES: {len(eligible)} < {min_screenable}")
    if boundary_tied:
        reasons.append("TOP_N_CUTOFF_TIED: no unique Top N")
    if symbols_allowed is None:
        reasons.append("NO_EXPLICIT_CURRENT_LISTING_MANIFEST")
    return {
        "schema": "atlaspm_selection_20_signal_consensus_v1",
        "as_of": as_of,
        "method": "original_20_signal_price_only_five_family_percentile_consensus",
        "research_only": True,
        "live_execution_enabled": False,
        "automated_handoff_approved": False,
        "alpha_validated": False,
        "status": "REVIEW_REQUIRED",
        "review_reasons": reasons,
        "top_requested": top,
        "top_n_unambiguous": not boundary_tied,
        "selected": results[:top] if not boundary_tied and len(eligible) >= min_screenable else [],
        "preview_not_trade_instructions": results[:max(20, top + 10)],
        "n_signals": len(SIGNALS),
        "n_families": len(SIGNAL_FAMILIES),
        "signal_families": {k: list(v) for k, v in SIGNAL_FAMILIES.items()},
        "family_weights": dict(FAMILY_WEIGHTS),
        "minimum_adjusted_close": min_price,
        "suspect_daily_price_ratio_threshold": max_daily_ratio,
        "decision_universe_count": len(symbols_allowed) if symbols_allowed is not None else None,
        "data_quality": diagnostics,
        "cautions": [
            "Relative percentile ranks are NOT calibrated forward returns or probabilities.",
            "Family voting limits correlated features' weight but does not imply independence.",
            "Anomalous jumps can reflect genuine corporate actions or unusual returns. Flag, investigate, never cherry-pick.",
            "Close-only series cannot compute OHLC, RSI using high/low, volume confirmation, or liquidity.",
            "This output is research, not trading signals; broker execution and alpha approval remain disabled.",
        ],
    }
