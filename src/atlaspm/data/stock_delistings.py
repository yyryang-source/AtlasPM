"""Load documented cash-only terminal events for a historical stock simulation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from math import isfinite
from pathlib import Path


@dataclass(frozen=True)
class StockDelisting:
    last_trade_date: date
    cash_date: date
    return_from_last_close: float
    last_close: float


def load_stock_delistings(
    path: Path,
    prices: dict[str, dict[date, float]],
    sessions: list[date],
) -> dict[str, StockDelisting]:
    required_columns = {"symbol", "last_trade_date", "cash_date", "return_from_last_close"}
    session_set = set(sessions)
    events: dict[str, StockDelisting] = {}
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not required_columns.issubset(reader.fieldnames):
            raise ValueError("Delistings CSV needs: symbol,last_trade_date,cash_date,return_from_last_close")
        for number, row in enumerate(reader, 2):
            try:
                symbol = row["symbol"].strip().upper()
                last_trade = date.fromisoformat(row["last_trade_date"].strip())
                cash_date = date.fromisoformat(row["cash_date"].strip())
                terminal_return = float(row["return_from_last_close"])
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid delisting CSV row {number}") from exc
            if not symbol or cash_date <= last_trade or not isfinite(terminal_return) or terminal_return < -1:
                raise ValueError(f"Invalid delisting symbol, cash date or return at row {number}")
            if symbol in seen:
                raise ValueError(f"Duplicate delisting for {symbol}; use distinct identifiers for ticker reuse")
            seen.add(symbol)
            if not sessions[0] <= last_trade <= sessions[-1]:
                continue
            history = prices.get(symbol, {})
            if last_trade not in session_set or last_trade not in history:
                raise ValueError(f"Delisting {symbol} requires a benchmark session and a close on {last_trade}")
            if max(history) != last_trade:
                raise ValueError(f"Delisting {symbol} conflicts with a close after {last_trade}")
            last_close = history[last_trade]
            if not isfinite(last_close * (1 + terminal_return)):
                raise ValueError(f"Delisting {symbol} has non-finite terminal proceeds")
            events[symbol] = StockDelisting(last_trade, cash_date, terminal_return, last_close)
    return events
