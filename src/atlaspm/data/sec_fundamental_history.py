"""Produce publication-dated factors from cached SEC Company Facts and raw closes."""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
import csv
from datetime import date
from gzip import open as gzopen
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any

from atlaspm.data.historical import load_adjusted_prices
from atlaspm.data.stock_fundamentals_import import _ratios
from atlaspm.strategies.equity.stock_screener import FUNDAMENTAL_COLUMNS, fundamental_value_issue


_REVENUE = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax")
_EQUITY = ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")
_INCOME = ("NetIncomeLoss", "ProfitLoss")
_OPERATING = ("OperatingIncomeLoss",)
_CF = ("NetCashProvidedByUsedInOperatingActivities",)
_CAPEX = ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
          "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets")
_DA = ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
       "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment")
_CASH = ("CashAndCashEquivalentsAtCarryingValue",)
_DEBT_TOTAL = ("LongTermDebt", "LongTermDebtAndCapitalLeaseObligations")
_DEBT_SHORT = ("ShortTermBorrowings", "ShortTermDebtCurrent", "ShortTermDebt", "CommercialPaper", "CommercialPaperCurrent")
_DEBT_CURRENT = ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent")
_DEBT_LONG = ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligationsNoncurrent")
_DOLLAR_TAGS = set(_REVENUE + _EQUITY + _INCOME + _OPERATING + _CF + _CAPEX + _DA + _CASH
                   + _DEBT_TOTAL + _DEBT_SHORT + _DEBT_CURRENT + _DEBT_LONG)
_SHARES = "EntityCommonStockSharesOutstanding"

Facts = dict[tuple[str, date | None, date], set[float]]


def _number(facts: Facts, tags: tuple[str, ...], start: date | None, end: date) -> float | None:
    for tag in tags:
        values = facts.get((tag, start, end), set())
        if len(values) == 1:
            return next(iter(values))
    return None


def _annual_facts(data: dict[str, Any], first: date, last: date) -> dict[tuple[date, str], Facts]:
    by_filing: dict[tuple[date, str], Facts] = defaultdict(lambda: defaultdict(set))
    taxonomy = data["facts"]
    metrics = [(tag, "USD", entry) for tag, entry in taxonomy.get("us-gaap", {}).items() if tag in _DOLLAR_TAGS]
    metrics.append((_SHARES, "shares", taxonomy.get("dei", {}).get(_SHARES, {})))
    for tag, units, definition in metrics:
        for observation in definition.get("units", {}).get(units, []):
            if observation.get("form") not in {"10-K", "10-K/A"} or "filed" not in observation:
                continue
            filed = date.fromisoformat(observation["filed"])
            if not first <= filed <= last:
                continue
            end = date.fromisoformat(observation["end"])
            start = date.fromisoformat(observation["start"]) if "start" in observation else None
            number = float(observation["val"])
            if end <= filed and isfinite(number):
                by_filing[filed, observation["accn"]][tag, start, end].add(number)
    return by_filing


def _annual_amounts(facts: Facts, filed: date) -> tuple[date, date, float, dict[str, float]] | None:
    annual_periods = {(start, end) for tag, start, end in facts if tag in _REVENUE and start is not None
                      and 350 <= (end - start).days + 1 <= 380}
    if not annual_periods:
        return None
    begin, end = max(annual_periods, key=lambda dates: (dates[1], dates[0]))
    prior = sorted((pair for pair in annual_periods if 350 <= (end - pair[1]).days <= 380),
                   key=lambda pair: (abs((end - pair[1]).days - 365), pair[1], pair[0]))
    if not prior or (filed - end).days > 200:
        return None
    prior_begin, prior_end = prior[0]
    values = {"net_income_usd": _number(facts, _INCOME, begin, end),
              "operating_cash_flow_usd": _number(facts, _CF, begin, end),
              "capex_usd": _number(facts, _CAPEX, begin, end),
              "operating_income_usd": _number(facts, _OPERATING, begin, end),
              "depreciation_amortization_usd": _number(facts, _DA, begin, end),
              "cash_usd": _number(facts, _CASH, None, end)}
    for alternatives, current_key, prior_key, start, prior_start in (
        (_REVENUE, "revenue_usd", "prior_revenue_usd", begin, prior_begin),
        (_EQUITY, "equity_usd", "prior_equity_usd", None, None),
    ):
        for tag in alternatives:
            current = _number(facts, (tag,), start, end)
            previous = _number(facts, (tag,), prior_start, prior_end)
            if current is not None and previous is not None:
                values[current_key], values[prior_key] = current, previous
                break
        else:
            return None
    debt = _number(facts, _DEBT_TOTAL, None, end)
    debt_parts = [_number(facts, names, None, end) for names in (_DEBT_CURRENT, _DEBT_LONG)] if debt is None else [debt]
    debt_parts.append(_number(facts, _DEBT_SHORT, None, end))
    denominated_debt = [part for part in debt_parts if part is not None]
    if not denominated_debt or any(part < 0 for part in denominated_debt) or any(v is None for v in values.values()):
        return None
    values["debt_usd"] = sum(denominated_debt)
    if any(values[key] < 0 for key in ("revenue_usd", "prior_revenue_usd", "capex_usd", "cash_usd",
                                       "depreciation_amortization_usd")):
        return None
    share_days = sorted((day for tag, start, day in facts if tag == _SHARES and start is None
                         and end <= day <= filed and (filed - day).days <= 120), reverse=True)
    if not share_days:
        return None
    share_day = share_days[0]
    shares = _number(facts, (_SHARES,), None, share_day)
    if shares is None or shares <= 0:
        return None
    return end, share_day, shares, values


def import_sec_factors(
    securities_path: Path, facts_directory: Path, raw_path: Path, benchmark_path: Path,
    first: date, last: date, destination: Path, *, benchmark_symbol: str = "SPY",
) -> dict:
    calendar = sorted(load_adjusted_prices(benchmark_path)[benchmark_symbol.strip().upper()])
    if first >= last or first < calendar[0] or last > calendar[-1]:
        raise ValueError("SEC factor import requires an ordered filing window within benchmark prices")
    securities = {}
    with securities_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"symbol", "cik", "sector"}.issubset(reader.fieldnames or ()):
            raise ValueError("SEC factor security map needs symbol,cik,sector")
        for row in reader:
            symbol = row["symbol"].strip().upper()
            cik, sector = row["cik"].strip(), row["sector"].strip()
            if not symbol or symbol in securities or not re.fullmatch(r"[0-9]{1,10}", cik) or not sector:
                raise ValueError(f"Invalid or duplicate SEC identity for {symbol}")
            securities[symbol] = (cik.zfill(10), sector)
    cik_counts = Counter(cik for cik, _ in securities.values())

    prices = {}
    with raw_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"date", "symbol", "close"}.issubset(reader.fieldnames or ()):
            raise ValueError("SEC factors need unadjusted price columns date,symbol,close")
        for row in reader:
            symbol = row["symbol"].strip().upper()
            if symbol not in securities:
                continue
            day = date.fromisoformat(row["date"])
            close = float(row["close"])
            if (symbol, day) in prices or not isfinite(close) or close <= 0:
                raise ValueError(f"Invalid or duplicate unadjusted close for {symbol} on {day}")
            prices[symbol, day] = close

    snapshots = {}
    skipped: Counter[str] = Counter()
    for symbol, (cik, sector) in sorted(securities.items()):
        file = facts_directory / f"CIK{cik}.json.gz"
        if cik_counts[cik] != 1:
            skipped["MULTIPLE_MAPPED_SHARE_CLASSES"] += 1
            continue
        if not file.is_file():
            skipped["MISSING_ISSUER_FACTS"] += 1
            continue
        with gzopen(file, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
        if int(data["cik"]) != int(cik):
            raise ValueError(f"SEC facts CIK mismatch for {symbol}")
        latest_period = date.min
        for (filed, accession), facts in sorted(_annual_facts(data, first, last).items()):
            index = bisect_right(calendar, filed)
            if index == len(calendar) or (calendar[index] - filed).days > 14:
                skipped["NO_NEXT_BENCHMARK_CLOSE"] += 1
                continue
            available = calendar[index]
            amounts = _annual_amounts(facts, filed)
            if amounts is None:
                skipped["INCOMPLETE_OR_INCOMPARABLE_FILING"] += 1
                continue
            end, shares_date, shares, values = amounts
            if end < latest_period:
                skipped["OLDER_PERIOD_AMENDMENT"] += 1
                continue
            latest_period = end
            price = prices.get((symbol, available))
            if price is None:
                skipped["MISSING_PUBLICATION_CLOSE"] += 1
                continue
            ratios = _ratios(values, shares * price)
            if ratios is None or fundamental_value_issue(ratios):
                skipped["UNSUITABLE_FACTOR_VALUES"] += 1
                continue
            snapshots[available, symbol] = {"as_of_date": available, "symbol": symbol, "sector": sector, **ratios,
                                             "cik": cik, "accession": accession, "filed_date": filed,
                                             "period_end": end, "reported_shares_date": shares_date,
                                             "reported_shares": shares, "unadjusted_close": price}
    if not snapshots:
        raise ValueError(f"No usable SEC factor snapshots: {dict(skipped)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    provenance = ("cik", "accession", "filed_date", "period_end", "reported_shares_date", "reported_shares", "unadjusted_close")
    with destination.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FUNDAMENTAL_COLUMNS + provenance)
        writer.writeheader()
        writer.writerows(record for _, record in sorted(snapshots.items()))
    return {"input_symbols": len(securities), "symbols_with_snapshots": len({symbol for _, symbol in snapshots}),
            "snapshots": len(snapshots), "excluded": dict(sorted(skipped.items())),
            "availability": "first benchmark close strictly after the SEC filing date"}
