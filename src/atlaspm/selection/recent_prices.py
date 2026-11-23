"""Bounded-memory close-history loader for monthly stock-ranking inference.

This is not a replacement for full-history training, backtests, or exchange
universe audits. On chronologically ordered per-symbol rows (ticker-major or
date-major), retain only the last 253 prices on or before the decision date.
For any out-of-order or duplicate date, defer to the canonical strict loader:
it sorts valid unordered input and still rejects duplicate records. That
fallback sacrifices the memory advantage instead of changing data semantics.
"""

from __future__ import annotations

import csv
from collections import defaultdict, deque
from datetime import date
from math import isfinite
from pathlib import Path

from atlaspm.quant.historical import load_adjusted_prices

_FEATURE_WINDOW = 253


def load_recent_adjusted_prices(
    path: Path, *, as_of: date,
) -> dict[str, dict[date, float]]:
    """Load only the closes actually used by ranking features.

    Every input row is validated, INCLUDING rows after as_of. Duplicates
    and nonchronological per-symbol data trigger a strict full-file fallback
    so results and fail-closed behavior match load_adjusted_prices().
    Each output includes all encountered symbols, even if a symbol has no
    pre-cutoff observations (important for universe/exclusion counts).
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
                observed_date = date.fromisoformat(record["date"].strip())
                symbol = record["symbol"].strip().upper()
                close = float(record["adjusted_close"])
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid price CSV row {line_no}") from exc
            if not symbol or not isfinite(close) or close <= 0:
                raise ValueError(
                    f"Invalid symbol or adjusted_close in row {line_no}"
                )
            previous = last_seen.get(symbol)
            if previous is not None and observed_date <= previous:
                unordered = True
                break
            last_seen[symbol] = observed_date
            # Keep the ticker in the historical universe even when its
            # available closes are all later than the decision date.
            history = recent[symbol]
            if observed_date <= as_of:
                history.append((observed_date, close))
    if unordered:
        # Exact compatibility with the full loader, including duplicate
        # rejection even for dates too old to fit the 253-close window.
        full = load_adjusted_prices(Path(path))
        return {
            symbol: dict(sorted(
                ((day, close) for day, close in observations.items()
                 if day <= as_of)
            )[-_FEATURE_WINDOW:])
            for symbol, observations in full.items()
        }
    if not recent:
        raise ValueError("No valid price rows in CSV")
    return {symbol: dict(history) for symbol, history in recent.items()}
