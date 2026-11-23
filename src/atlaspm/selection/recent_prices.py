"""Bounded-memory adjusted-close arrays for month-end ranking inference.

Retains at most 253 decision-date prices per ticker for the nine ranking
features. Model training and walk-forward backtests MUST continue using the
full-history loader. Out-of-order dates fall back to the canonical strict
array loader, preserving valid unordered input and duplicate rejection.
"""

from __future__ import annotations

import csv
from collections import defaultdict, deque
from datetime import date
from math import isfinite
from pathlib import Path

from atlaspm.selection.price_arrays import load_stock_price_arrays

_FEATURE_WINDOW = 253


def load_recent_stock_price_arrays(
    path: Path, *, as_of: date,
) -> dict[str, tuple[list[date], list[float]]]:
    """Return symbol -> (dates, adjusted_closes) as of a completed close.

    Per-symbol chronological CSV is streamed without storing earlier bars.
    For unordered/duplicate per-ticker dates, the existing full-array loader
    is used: it accepts unordered input and rejects duplicates. All rows
    (even future-dated) are validated, avoiding silent data-quality changes.
    """
    if not isinstance(as_of, date):
        raise ValueError("as_of must be a datetime.date")
    recent: dict[str, deque[tuple[date, float]]] = defaultdict(
        lambda: deque(maxlen=_FEATURE_WINDOW)
    )
    last_seen: dict[str, date] = {}
    unordered = False
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {
            "date", "symbol", "adjusted_close"
        }.issubset(reader.fieldnames):
            raise ValueError("CSV needs columns: date,symbol,adjusted_close")
        for line_no, record in enumerate(reader, start=2):
            try:
                day = date.fromisoformat(record["date"].strip())
                symbol = record["symbol"].strip().upper()
                close = float(record["adjusted_close"])
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid price CSV row {line_no}") from exc
            if not symbol or not isfinite(close) or close <= 0:
                raise ValueError(
                    f"Invalid symbol or adjusted_close in row {line_no}"
                )
            previous = last_seen.get(symbol)
            if previous is not None and day <= previous:
                unordered = True
                break
            last_seen[symbol] = day
            # Include symbols whose only observations are after as_of so
            # the candidate universe and exclusion counts do not change.
            series = recent[symbol]
            if day <= as_of:
                series.append((day, close))
    if unordered:
        full = load_stock_price_arrays(Path(path))
        result = {}
        for symbol, (days, closes) in full.items():
            # Old data may be unordered, but the full reader sorted it.
            # Preserve values exactly; no forward-fill or split adjustment.
            retained = [
                (day, close) for day, close in zip(days, closes)
                if day <= as_of
            ][-_FEATURE_WINDOW:]
            result[symbol] = (
                [day for day, _ in retained],
                [close for _, close in retained],
            )
        return result
    if not recent:
        raise ValueError("No valid price rows in CSV")
    return {
        symbol: ([day for day, _ in series], [close for _, close in series])
        for symbol, series in recent.items()
    }
