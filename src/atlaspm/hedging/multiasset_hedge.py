"""Compare funded stock, stock/Treasury and stock/Treasury/put portfolios."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date
from math import floor, isfinite
from typing import Any
from zoneinfo import ZoneInfo

from atlaspm.data.option_history import OptionHistory
from atlaspm.strategies.options.protective_put import ProtectivePutSettings, StockMark, compare_protective_put, nav_performance
from atlaspm.data.treasury_history import TreasuryHistory
from atlaspm.strategies.bonds.treasury_replay import replay_treasury_holding


_NEW_YORK = ZoneInfo("America/New_York")
_ARMS = ("stock_only", "stock_treasury", "stock_treasury_put")
_OPTION_COSTS = ("premium_paid_usd", "premium_sold_usd", "execution_fees_usd", "spread_cost_vs_mid_usd")


@dataclass(frozen=True)
class MultiassetSettings:
    initial_capital_usd: float
    minimum_cash_usd: float
    treasury_cusip: str
    treasury_face_usd: int
    stock_entry_cost_bps: float = 5
    treasury_entry_cost_bps: float = 5
    treasury_shock_bps: float = 100
    treasury_max_quote_age_days: int = 3


def compare_multiasset_hedge(
    option_history: OptionHistory,
    treasury_history: TreasuryHistory,
    marks: list[StockMark],
    put_settings: ProtectivePutSettings,
    settings: MultiassetSettings,
    *,
    treasury_closed: frozenset[date] = frozenset(),
    stress_window: tuple[date, date] | None = None,
) -> dict[str, Any]:
    capital = settings.initial_capital_usd
    minimum_cash = settings.minimum_cash_usd
    entry_rates = (settings.stock_entry_cost_bps / 10000, settings.treasury_entry_cost_bps / 10000)
    if (len(marks) < 3 or not isfinite(capital) or not isfinite(minimum_cash) or not 0 <= minimum_cash < capital
            or any(not 0 <= fee < 1 for fee in entry_rates)):
        raise ValueError("Invalid multi-asset opening budget or execution costs")
    sessions = [mark.at.astimezone(_NEW_YORK).date() for mark in marks]
    if sessions[0] in treasury_closed or not treasury_closed.issubset(sessions):
        raise ValueError("Treasury closed dates must be in-window stock sessions after the opening day")
    if stress_window is not None:
        start, end = stress_window
        if start >= end or start not in sessions or end not in sessions:
            raise ValueError("Stress window needs two ordered stock session dates from the backtest")

    treasury = replay_treasury_holding(
        treasury_history, settings.treasury_cusip, settings.treasury_face_usd, sessions,
        max_quote_age_days=settings.treasury_max_quote_age_days, shock_bps=settings.treasury_shock_bps,
        include_opening_date_payment=False,
    )
    carried = []
    for row, session in zip(treasury["valuations"], sessions, strict=True):
        if row["held_face_usd"] and row["date"] != row["quote_date"]:
            if session not in treasury_closed:
                raise ValueError(f"Missing Treasury close on open session {session}")
            carried.append({"date": row["date"], "quote_date": row["quote_date"]})

    stock_fee_rate, treasury_fee_rate = entry_rates
    first_price = marks[0].close
    stock_only_shares = floor((capital - minimum_cash) / (first_price * (1 + stock_fee_rate)))
    only_stock_cost = stock_only_shares * first_price
    only_fee = only_stock_cost * stock_fee_rate
    stock_only_cash = capital - only_stock_cost - only_fee
    mixed_shares = put_settings.shares
    mixed_stock_cost = mixed_shares * first_price
    mixed_treasury_cost = treasury["valuations"][0]["dirty_market_value_usd"]
    mixed_fee = mixed_stock_cost * stock_fee_rate + mixed_treasury_cost * treasury_fee_rate
    mixed_cash = capital - mixed_stock_cost - mixed_treasury_cost - mixed_fee
    if mixed_shares < 1 or stock_only_shares < mixed_shares or mixed_cash < minimum_cash:
        raise ValueError("Mixed allocation must fit the initial capital and minimum cash reserve")
    opening = {
        "stock_only": {"stock_shares": stock_only_shares, "stock_market_value_usd": only_stock_cost,
                       "treasury_dirty_value_usd": 0, "cash_usd": stock_only_cash, "entry_cost_usd": only_fee},
        "stock_treasury": {"stock_shares": mixed_shares, "stock_market_value_usd": mixed_stock_cost,
                           "treasury_dirty_value_usd": mixed_treasury_cost, "cash_usd": mixed_cash,
                           "entry_cost_usd": mixed_fee},
    }
    opening["stock_treasury_put"] = opening["stock_treasury"].copy()
    hedge = compare_protective_put(option_history, marks, replace(put_settings, cash_usd=mixed_cash))

    curve = []
    for treasury_mark, hedge_mark in zip(treasury["valuations"], hedge["curve"], strict=True):
        stock_only_value = hedge_mark["stock_close"] * stock_only_shares
        stock_only_dividends = hedge_mark["dividends_accrued_usd"] * stock_only_shares / mixed_shares
        stock_only_nav = stock_only_value + stock_only_dividends + stock_only_cash
        treasury_nav = treasury_mark["nav_usd"]
        mixed_nav = hedge_mark["unhedged_nav_usd"] + treasury_nav
        hedged_nav = hedge_mark["hedged_nav_usd"] + treasury_nav
        curve.append({"date": treasury_mark["date"], "at": hedge_mark["at"], "stock_close": hedge_mark["stock_close"],
                      "stock_only_nav_usd": stock_only_nav, "stock_treasury_nav_usd": mixed_nav,
                      "stock_treasury_put_nav_usd": hedged_nav,
                      "stock_only_gross_equity_weight": stock_only_value / stock_only_nav,
                      "stock_treasury_gross_equity_weight": hedge_mark["stock_value_usd"] / mixed_nav,
                      "stock_treasury_put_gross_equity_weight": hedge_mark["stock_value_usd"] / hedged_nav,
                      "protected_shares": hedge_mark["covered_shares"], "option_value_usd": hedge_mark["option_bid_value_usd"],
                      "option_sale_receivable_usd": hedge_mark["option_sale_receivable_usd"],
                      "treasury_nav_usd": treasury_nav, "treasury_dv01_usd": treasury_mark["dv01_usd"],
                      "treasury_rate_shock_pnl_usd": treasury_mark["rate_shock_pnl_usd"],
                      "treasury_quote_date": treasury_mark["quote_date"],
                      "stock_treasury_put_available_cash_usd": hedge_mark["cash_available_usd"] + treasury_mark["cash_available_usd"]})

    def summarize(series: list[dict[str, Any]], *, initial_nav: float | None = None) -> dict[str, dict[str, float]]:
        return {arm: nav_performance(series, f"{arm}_nav_usd", initial_nav=initial_nav) for arm in _ARMS}

    stress = None
    if stress_window is not None:
        series = curve[sessions.index(stress_window[0]):sessions.index(stress_window[1]) + 1]
        stress = {"start": series[0]["date"], "end": series[-1]["date"], "sessions": len(series),
                  "performance": summarize(series)}
    costs = {key: hedge["option_replay"][key] for key in _OPTION_COSTS}
    costs["net_cost_after_sale_usd"] = costs["premium_paid_usd"] - costs["premium_sold_usd"] + costs["execution_fees_usd"]
    return {"method": "matched_capital_stock_treasury_put", "settings": asdict(settings), "opening": opening,
            "performance": summarize(curve, initial_nav=capital), "stress_window": stress, "curve": curve,
            "treasury_replay": treasury, "put_replay": hedge, "option_costs": costs,
            "carried_treasury_quotes": carried, "live_execution_enabled": False}
