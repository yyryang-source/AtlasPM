"""Compact price-history ingestion for large US stock ranking research.

Unlike the generic portfolio loader, this path never materializes a
date->close dict per stock just to rebuild an array immediately afterward.
It retains the same strict input validation, date ordering and duplicate
detection. It does not alter or normalize financial data or imply tradability.
"""

from __future__ import annotations

import csv
from bisect import bisect_right
from collections import deque
from datetime import date
from math import isfinite
from pathlib import Path


def load_stock_price_arrays(
    path: Path,
) -> dict[str, tuple[list[date], list[float]]]:
    """Read long adjusted-close CSV into chronologically ordered stock arrays.

    Most `stock-sync-market` exports are already chronological within each
    ticker. Those symbols need no sorting. If CSV rows are shuffled or
    date-major interleaved, we sort only tickers that arrive out of order.

    The generic historical loader rejects duplicate (symbol, day) pairs;
    this loader preserves that behavior, including nonadjacent duplicates.
    All price rows remain validated, including future-dated observations.
    """
    histories: dict[str, tuple[list[date], list[float]]] = {}
    out_of_order: set[str] = set()
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {
            "date", "symbol", "adjusted_close"
        }.issubset(reader.fieldnames):
            raise ValueError("CSV needs columns: date,symbol,adjusted_close")

        for line_no, record in enumerate(reader, start=2):
            try:
                observed = date.fromisoformat(record["date"].strip())
                symbol = record["symbol"].strip().upper()
                close = float(record["adjusted_close"])
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid price CSV row {line_no}") from exc
            if not symbol or not isfinite(close) or close <= 0:
                raise ValueError(f"Invalid symbol or adjusted_close in row {line_no}")

            history = histories.get(symbol)
            if history is None:
                history = ([], [])
                histories[symbol] = history
            dates, prices = history
            if dates and observed <= dates[-1]:
                out_of_order.add(symbol)
            dates.append(observed)
            prices.append(close)

    if not histories:
        raise ValueError("No valid price rows in CSV")

    # Sort only affected ticker histories; checking neighbors after sorting
    # detects duplicates even when identical dates were far apart in the CSV.
    for symbol in out_of_order:
        dates, prices = histories[symbol]
        ordered = sorted(zip(dates, prices), key=lambda item: item[0])
        for previous, current in zip(ordered, ordered[1:]):
            if previous[0] == current[0]:
                raise ValueError(f"Duplicate price for {symbol} on {current[0]}")
        dates[:] = (item[0] for item in ordered)
        prices[:] = (item[1] for item in ordered)
    return histories


def load_recent_stock_price_arrays(
    path: Path, *, as_of: date,
) -> dict[str, tuple[list[date], list[float]]]:
    """Return at most 253 completed closes per stock for ranking inference.

    This is an optional *inference-only* path. Training labels must continue
    using load_stock_price_arrays() and all available historical observations.
    Price validation includes rows after as_of, although those prices are
    never used to compute candidates.

    Most quote exports are chronological *within each ticker* (ticker-major
    or date-major). Such inputs use bounded rolling windows directly.
    Shuffled-ticker histories fall back to the existing full array reader,
    preserving its ordering semantics and strict nonadjacent duplicate
    detection rather than selecting an arbitrary 253-price subset.
    """
    if not isinstance(as_of, date):
        raise ValueError("as_of must be a date")
    recent: dict[str, deque[tuple[date, float]]] = {}
    last_date: dict[str, date] = {}
    nonchronological = False
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {
            "date", "symbol", "adjusted_close"
        }.issubset(reader.fieldnames):
            raise ValueError("CSV needs columns: date,symbol,adjusted_close")
        for line_no, record in enumerate(reader, start=2):
            try:
                observed = date.fromisoformat(record["date"].strip())
                symbol = record["symbol"].strip().upper()
                close = float(record["adjusted_close"])
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid price CSV row {line_no}") from exc
            if not symbol or not isfinite(close) or close <= 0:
                raise ValueError(f"Invalid symbol or adjusted_close in row {line_no}")
            previous = last_date.get(symbol)
            if previous is not None and observed <= previous:
                nonchronological = True
            last_date[symbol] = observed
            window = recent.setdefault(symbol, deque(maxlen=253))
            if observed <= as_of:
                window.append((observed, close))

    if not recent:
        raise ValueError("No valid price rows in CSV")
    if nonchronological:
        # Preserve PR #38's semantics for unsorted CSVs. The full reader
        # validates duplicate (symbol, date) pairs even when far apart.
        histories = load_stock_price_arrays(path)
        selected = {}
        for symbol, (dates, closes) in histories.items():
            end = bisect_right(dates, as_of)
            start = max(0, end - 253)
            selected[symbol] = (dates[start:end], closes[start:end])
        return selected

    return {
        symbol: (
            [day for day, _ in window],
            [close for _, close in window],
        )
        for symbol, window in recent.items()
    }
