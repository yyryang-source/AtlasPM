"""Compare rolling protective puts with the same pre-funded stock position."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from atlaspm.data.option_history import OptionHistory, option_timestamp
from atlaspm.strategies.options.option_replay import replay_long_options


_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class StockMark:
    at: datetime
    close: float
    sale_cash_at: datetime
    dividend_per_share: float


@dataclass(frozen=True)
class ProtectivePutSettings:
    underlying: str
    shares: int
    cash_usd: float
    fee_per_contract_usd: float = 1.0
    otm_fraction: float = 0.05
    target_dte: int = 45
    max_dte: int = 65
    roll_sessions: int = 21
    max_quote_age_minutes: int = 1
    eod_snapshot: bool = False


def load_stock_marks(path: Path) -> list[StockMark]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"observed_at", "close", "sale_cash_at"}.issubset(reader.fieldnames or ()):
            raise ValueError("Stock marks need observed_at,close,sale_cash_at")
        marks = [StockMark(option_timestamp(row["observed_at"]), float(row["close"]),
                           option_timestamp(row["sale_cash_at"]), float(row.get("dividend_per_share") or 0))
                 for row in reader]
    prior = None
    for mark in marks:
        session = mark.at.astimezone(_NEW_YORK).date()
        if (not isfinite(mark.close) or mark.close <= 0 or not isfinite(mark.dividend_per_share)
                or mark.dividend_per_share < 0 or mark.sale_cash_at.astimezone(_NEW_YORK).date() <= session
                or (prior is not None and session <= prior)):
            raise ValueError("Stock marks require sorted unique sessions, positive prices and later sale settlement")
        prior = session
    if len(marks) < 3:
        raise ValueError("Protective put comparison requires at least three sessions")
    return marks


def _choose_put(history: OptionHistory, settings: ProtectivePutSettings, signal: StockMark,
                exit_at: datetime) -> tuple[str, int]:
    target_strike = signal.close * (1 - settings.otm_fraction)
    session = signal.at.astimezone(_NEW_YORK).date()
    max_age = timedelta(minutes=settings.max_quote_age_minutes)
    candidates = []
    for key, contract in history.contracts.items():
        if (contract.underlying != settings.underlying or contract.right != "put"
                or contract.settlement_type != "physical" or contract.strike > target_strike
                or min(contract.last_trade_at, contract.expiration_at) <= exit_at
                or settings.shares % contract.multiplier):
            continue
        days = (contract.expiration_at.astimezone(_NEW_YORK).date() - session).days
        if days > settings.max_dte:
            continue
        try:
            quote = history.quote_at(key, signal.at, max_age)
        except ValueError:
            continue
        quantity = settings.shares // contract.multiplier
        if quote.ask_size >= quantity and quote.bid_size >= quantity and quote.bid > 0:
            candidates.append((abs(days - settings.target_dte), contract.expiration_at, -contract.strike,
                               key, quantity))
    if not candidates:
        raise ValueError(f"No eligible put with a two-sided quote at the signal on {session}")
    _, _, _, key, quantity = min(candidates)
    return key, quantity


def nav_performance(curve: list[dict[str, Any]], nav_key: str, *, initial_nav: float | None = None) -> dict[str, float]:
    start = peak = curve[0][nav_key] if initial_nav is None else initial_nav
    drawdown = 0.0
    for row in curve:
        level = row[nav_key]
        peak = max(peak, level)
        drawdown = min(drawdown, level / peak - 1)
    end = curve[-1][nav_key]
    return {"ending_nav_usd": end, "total_return": end / start - 1, "max_drawdown": drawdown}


def compare_protective_put(history: OptionHistory, marks: list[StockMark],
                           settings: ProtectivePutSettings) -> dict[str, Any]:
    if (len(marks) < 3 or not settings.underlying or settings.shares < 1 or settings.roll_sessions < 1
            or settings.max_quote_age_minutes < 1 or not 1 <= settings.target_dte <= settings.max_dte
            or not 0 <= settings.otm_fraction < 1):
        raise ValueError("Invalid protective put settings")

    decisions = []
    orders = []
    held = None
    for index in range(0, len(marks) - 2, settings.roll_sessions):
        signal, execution = marks[index], marks[index + 1]
        # Close the put before expiry to keep the same stock exposure in both arms.
        next_exit = marks[min(index + settings.roll_sessions + 1, len(marks) - 1)].at
        key, quantity = _choose_put(history, settings, signal, next_exit)
        if held != (key, quantity):
            if held is not None:
                orders.append({"at": execution.at.isoformat(), "contract_id": held[0], "side": "sell",
                               "contracts": held[1], "cash_at": execution.sale_cash_at.isoformat()})
            orders.append({"at": execution.at.isoformat(), "contract_id": key, "side": "buy", "contracts": quantity})
        decisions.append({"signal_at": signal.at.isoformat(), "effective_at": execution.at.isoformat(),
                          "contract_id": key, "contracts": quantity, "replaced": held != (key, quantity)})
        held = (key, quantity)

    assert held is not None
    end = marks[-1]
    orders.append({"at": end.at.isoformat(), "contract_id": held[0], "side": "sell", "contracts": held[1],
                   "cash_at": end.sale_cash_at.isoformat()})
    replay = replay_long_options(history, {
        "initial_cash_usd": settings.cash_usd, "initial_shares": {settings.underlying: settings.shares},
        "fee_per_contract_usd": settings.fee_per_contract_usd, "max_quote_age_minutes": settings.max_quote_age_minutes,
        "orders": orders, "valuation_times": [mark.at.isoformat() for mark in marks],
    })
    curve = []
    dividends = 0.0
    for mark, book in zip(marks, replay["valuations"], strict=True):
        if curve:
            dividends += mark.dividend_per_share * settings.shares
        stock_value = mark.close * settings.shares
        receivable = book["cash_receivable_usd"]
        budget_cash = book["cash_available_usd"]
        option_value = book["option_bid_value_usd"]
        curve.append({"at": mark.at.isoformat(), "stock_close": mark.close, "stock_value_usd": stock_value,
                      "dividends_accrued_usd": dividends, "cash_available_usd": budget_cash,
                      "option_sale_receivable_usd": receivable, "option_bid_value_usd": option_value,
                      "covered_shares": sum(history.contracts[key].multiplier * count
                                            for key, count in book["option_positions"].items()),
                      "hedged_nav_usd": stock_value + dividends + budget_cash + receivable + option_value,
                      "unhedged_nav_usd": stock_value + dividends + settings.cash_usd})
    return {"settings": asdict(settings),
            "method": "prior_session_signal_next_eod_snapshot" if settings.eod_snapshot else "prior_session_signal_next_snapshot",
            "hedged": nav_performance(curve, "hedged_nav_usd"), "unhedged": nav_performance(curve, "unhedged_nav_usd"),
            "decisions": decisions, "option_replay": replay, "curve": curve, "live_execution_enabled": False}
