"""Convert verified annual filing facts and dated valuations into factor snapshots."""

from __future__ import annotations

import csv
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from atlaspm.historical import load_adjusted_prices
from atlaspm.selection.stock_screener import FUNDAMENTAL_COLUMNS, fundamental_value_issue


_AMOUNTS = (
    "revenue_usd", "prior_revenue_usd", "net_income_usd", "equity_usd", "prior_equity_usd",
    "debt_usd", "operating_cash_flow_usd", "capex_usd", "operating_income_usd",
    "depreciation_amortization_usd", "cash_usd",
)
_FILING_COLUMNS = (
    "symbol", "sector", "accepted_at", "filed_date", "period_start", "period_end", "prior_period_end", "source_ref",
)
_MARKET_CAP_COLUMNS = ("date", "symbol", "market_cap_usd", "source_ref")
_PROVENANCE_COLUMNS = ("accepted_at", "filed_date", "period_end", "filing_source_ref", "market_cap_source_ref")


def _csv_rows(path: Path, required: tuple[str, ...]) -> Iterator[tuple[int, dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not set(required).issubset(reader.fieldnames):
            raise ValueError("CSV needs columns: " + ",".join(required))
        for number, record in enumerate(reader, 2):
            row = {field: (record[field] or "").strip() for field in required}
            if not all(row.values()):
                raise ValueError(f"Missing required CSV value at row {number}")
            yield number, row


def _ratios(v: dict[str, float], market_cap: float) -> dict[str, float] | None:
    average_equity = v["equity_usd"] / 2 + v["prior_equity_usd"] / 2
    ebitda = v["operating_income_usd"] + v["depreciation_amortization_usd"]
    if min(average_equity, ebitda, v["equity_usd"], v["prior_revenue_usd"]) <= 0:
        return None
    return {
        "market_cap_usd": market_cap,
        "roe": v["net_income_usd"] / average_equity,
        "revenue_growth_yoy": v["revenue_usd"] / v["prior_revenue_usd"] - 1,
        "debt_to_equity": v["debt_usd"] / v["equity_usd"],
        "fcf_yield": (v["operating_cash_flow_usd"] - v["capex_usd"]) / market_cap,
        "ev_to_ebitda": (market_cap + v["debt_usd"] - v["cash_usd"]) / ebitda,
    }


def import_stock_fundamentals(
    filings_path: Path,
    valuations_path: Path,
    benchmark_path: Path,
    destination: Path,
    *,
    benchmark_symbol: str = "SPY",
) -> dict[str, Any]:
    benchmark = benchmark_symbol.strip().upper()
    histories = load_adjusted_prices(benchmark_path)
    if benchmark not in histories:
        raise ValueError(f"Benchmark {benchmark} is missing from the calendar input")
    sessions = sorted(histories[benchmark])
    new_york = ZoneInfo("America/New_York")

    valuations: dict[tuple[date, str], tuple[float, str]] = {}
    for number, row in _csv_rows(valuations_path, _MARKET_CAP_COLUMNS):
        key = (date.fromisoformat(row["date"]), row["symbol"].upper())
        value = float(row["market_cap_usd"])
        if key in valuations or not isfinite(value) or value <= 0:
            raise ValueError(f"Invalid or duplicate market capitalization at row {number}")
        valuations[key] = (value, row["source_ref"])

    eligible_filings: list[tuple[date, str, date, datetime, dict[str, str], dict[str, float]]] = []
    seen = set()
    skipped: Counter[str] = Counter()
    input_count = 0
    for number, row in _csv_rows(filings_path, _FILING_COLUMNS + _AMOUNTS):
        input_count += 1
        accepted = datetime.fromisoformat(row["accepted_at"])
        if accepted.utcoffset() is None:
            raise ValueError(f"Filing accepted_at must include a UTC offset at row {number}")
        local_day = accepted.astimezone(new_york).date()
        filed = date.fromisoformat(row["filed_date"])
        start, end, prior = (date.fromisoformat(row[field]) for field in ("period_start", "period_end", "prior_period_end"))
        if not (350 <= (end - start).days + 1 <= 380 and 350 <= (end - prior).days <= 380
                and prior < start <= end <= local_day <= filed):
            raise ValueError(f"Invalid annual filing chronology or fiscal periods at row {number}")
        symbol = row["symbol"].upper()
        key = (symbol, end, accepted)
        if key in seen:
            raise ValueError(f"Duplicate release for {symbol} and fiscal period {end}")
        seen.add(key)

        amounts = {field: float(row[field]) for field in _AMOUNTS}
        nonnegative = ("revenue_usd", "prior_revenue_usd", "debt_usd", "capex_usd", "depreciation_amortization_usd", "cash_usd")
        if any(not isfinite(amount) for amount in amounts.values()) or any(amounts[field] < 0 for field in nonnegative):
            raise ValueError(f"Invalid annual USD amounts at row {number}")

        # Wait until a session strictly after the New York acceptance date and
        # no earlier than the official filing date. Require a known session
        # following acceptance instead of inferring one beyond the input.
        index = max(bisect_right(sessions, local_day), bisect_left(sessions, filed))
        if local_day < sessions[0] or index == len(sessions):
            skipped["OUTSIDE_BENCHMARK_CALENDAR"] += 1
            continue
        if (sessions[index] - max(local_day, filed)).days > 14:
            skipped["BENCHMARK_CALENDAR_GAP"] += 1
            continue
        eligible_filings.append((sessions[index], symbol, end, accepted, row, amounts))

    latest_period: dict[str, date] = {}
    snapshots: dict[tuple[date, str], dict[str, Any]] = {}
    for available, symbol, period, accepted, row, amounts in sorted(eligible_filings, key=lambda item: item[:4]):
        if symbol in latest_period and period < latest_period[symbol]:
            skipped["OLDER_PERIOD_AMENDMENT"] += 1
            continue
        latest_period[symbol] = period
        key = (available, symbol)
        if key in snapshots:
            del snapshots[key]
            skipped["SUPERSEDED_SAME_SESSION"] += 1
        valuation = valuations.get(key)
        if valuation is None:
            skipped["MISSING_PUBLICATION_SESSION_MARKET_CAP"] += 1
            continue
        market_cap, cap_ref = valuation
        values = _ratios(amounts, market_cap)
        if values is None or fundamental_value_issue(values) is not None:
            skipped["UNSUITABLE_RATIO_INPUTS"] += 1
            continue
        snapshots[key] = {
            "as_of_date": available.isoformat(), "symbol": symbol, "sector": row["sector"], **values,
            "accepted_at": accepted.isoformat(), "filed_date": row["filed_date"], "period_end": period.isoformat(),
            "filing_source_ref": row["source_ref"], "market_cap_source_ref": cap_ref,
        }

    if not snapshots:
        raise ValueError(f"No usable fundamental snapshots: {dict(skipped)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FUNDAMENTAL_COLUMNS + _PROVENANCE_COLUMNS)
        writer.writeheader()
        writer.writerows(row for _, row in sorted(snapshots.items()))
    return {
        "filing_count": input_count,
        "snapshot_count": len(snapshots),
        "snapshots_by_symbol": dict(Counter(symbol for _, symbol in snapshots)),
        "skipped": dict(skipped),
        "first_snapshot_date": min(day for day, _ in snapshots).isoformat(),
        "last_snapshot_date": max(day for day, _ in snapshots).isoformat(),
    }
