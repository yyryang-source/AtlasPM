"""Prepare research membership snapshots and source-backed cash exits."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
from datetime import date
from math import isfinite
from pathlib import Path

from atlaspm.data.historical import load_adjusted_prices


def _rows(path: Path, required: set[str]):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = reader.fieldnames or []
        if not required.issubset(names) or len(names) != len(set(names)):
            raise ValueError(f"{path.name} needs unique columns including {', '.join(sorted(required))}")
        for source in reader:
            yield {key: (source[key] or "").strip() for key in required}


@dataclass(frozen=True)
class MembershipSpan:
    symbol: str
    cik: str
    start: date
    end: date | None
    start_confidence: str


def import_membership_spans(spans_path: Path, benchmark_path: Path, start: date, end: date,
                            destination: Path, *, benchmark_symbol: str = "SPY") -> dict:
    benchmark = benchmark_symbol.strip().upper()
    history = load_adjusted_prices(benchmark_path)
    sessions = sorted(day for day in history[benchmark] if start <= day <= end)
    if not sessions or start < min(history[benchmark]) or end > max(history[benchmark]):
        raise ValueError("Supply benchmark sessions covering the membership import window")

    spans: dict[str, list[MembershipSpan]] = defaultdict(list)
    required = {"ticker", "cik", "start", "end", "start_confidence"}
    for row in _rows(spans_path, required):
        began = date.fromisoformat(row["start"])
        ceased = date.fromisoformat(row["end"]) if row["end"] else None
        if ceased is not None and ceased <= began:
            raise ValueError(f"Membership end must follow start for {row['ticker']}")
        if began > sessions[-1] or (ceased is not None and ceased <= sessions[0]):
            continue
        symbol = row["ticker"].upper()
        cik = row["cik"]
        if not symbol or not row["start_confidence"] or (cik and (not cik.isascii() or not cik.isdigit())):
            raise ValueError("In-window membership spans need a symbol, confidence and a valid CIK if supplied")
        if symbol != benchmark:
            spans[symbol].append(MembershipSpan(symbol, cik.zfill(10) if cik else "", began, ceased, row["start_confidence"]))
    ordered = []
    for symbol, periods in sorted(spans.items()):
        if len({span.cik for span in periods if span.cik}) > 1:
            raise ValueError(f"Membership ticker reuse for {symbol}; resolve identity before importing")
        periods.sort(key=lambda span: span.start)
        for earlier, later in zip(periods, periods[1:]):
            if earlier.end is None or earlier.end > later.start:
                raise ValueError(f"Overlapping membership spans for {symbol}")
        ordered.extend(periods)

    rows = []
    counts = []
    member_days = Counter()
    for day in sessions:
        active = [span for span in ordered if span.start <= day and (span.end is None or day < span.end)]
        if not active:
            raise ValueError(f"No historical membership on benchmark session {day}")
        counts.append(len(active))
        member_days.update(span.start_confidence for span in active)
        rows.extend((day, span.symbol, span.cik, span.start, span.start_confidence) for span in active)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["as_of_date", "symbol", "cik", "member_since", "start_confidence"])
        writer.writerows(rows)
    return {"first_session": str(sessions[0]), "last_session": str(sessions[-1]), "sessions": len(sessions),
            "unique_members": len(spans), "min_members": min(counts), "max_members": max(counts),
            "member_days_by_start_confidence": dict(sorted(member_days.items()))}


def import_cash_exits(events_path: Path, raw_path: Path, adjusted_path: Path, destination: Path) -> dict:
    adjusted_prices = load_adjusted_prices(adjusted_path)
    events = {}
    required = {"symbol", "last_trade_date", "cash_date", "cash_per_share", "source_ref"}
    for row in _rows(events_path, required):
        symbol = row["symbol"].upper()
        trade, cash = date.fromisoformat(row["last_trade_date"]), date.fromisoformat(row["cash_date"])
        payout = float(row["cash_per_share"])
        if (not symbol or symbol in events or not row["source_ref"] or cash <= trade
                or not isfinite(payout) or payout < 0):
            raise ValueError(f"Invalid or duplicate source-backed cash exit for {symbol}")
        series = adjusted_prices.get(symbol, {})
        if not series or max(series) != trade:
            raise ValueError(f"Adjusted-price history for {symbol} must end on its last trade date {trade}")
        events[symbol] = (trade, cash, payout, row["source_ref"])
    if not events:
        raise ValueError("No cash exit events supplied")

    raw_closes = {}
    for row in _rows(raw_path, {"date", "symbol", "close"}):
        symbol = row["symbol"].upper()
        if symbol not in events:
            continue
        day = date.fromisoformat(row["date"])
        last_trade = events[symbol][0]
        if day > last_trade:
            raise ValueError(f"Unadjusted-price history for {symbol} must end on {last_trade}")
        if day == last_trade:
            raw_close = float(row["close"])
            if symbol in raw_closes or not isfinite(raw_close) or raw_close <= 0:
                raise ValueError(f"Invalid or duplicate unadjusted close for {symbol} on {day}")
            raw_closes[symbol] = raw_close
    if set(events) != set(raw_closes):
        raise ValueError("Every cash exit needs an unadjusted close on its last trade date")

    output = []
    for symbol, (trade, cash, payout, ref) in sorted(events.items()):
        raw_close = raw_closes[symbol]
        terminal_return = payout / raw_close - 1
        if not isfinite(terminal_return):
            raise ValueError(f"Cash exit return is invalid for {symbol}")
        output.append((symbol, trade, cash, terminal_return, raw_close, payout, ref))

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "last_trade_date", "cash_date", "return_from_last_close",
                         "unadjusted_last_close", "cash_per_share", "source_ref"])
        writer.writerows(output)
    return {"events": len(events), "symbols": sorted(events)}
