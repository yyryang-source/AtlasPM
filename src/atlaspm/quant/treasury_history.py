"""Date-indexed terms, payment dates and clean prices for nominal Treasuries."""

from __future__ import annotations

from bisect import bisect_right
from calendar import monthrange
import csv
from dataclasses import dataclass
from datetime import date
from math import isfinite
from pathlib import Path
import re


def _rows(path: Path, required: set[str]):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        columns = rows.fieldnames or []
        if not required.issubset(columns) or len(columns) != len(set(columns)):
            raise ValueError(f"{path.name} requires unique columns including {', '.join(sorted(required))}")
        for number, row in enumerate(rows, 2):
            if not all(row[column] and row[column].strip() for column in required):
                raise ValueError(f"Missing Treasury value in {path.name} row {number}")
            yield row


def _semiannual_dates(maturity: date, dated: date) -> list[date]:
    day_number = maturity.day
    end_of_month = day_number == monthrange(maturity.year, maturity.month)[1]
    month_number = maturity.year * 12 + maturity.month - 1
    dates = [maturity]
    while dates[-1] > dated:
        month_number -= 6
        year, offset = divmod(month_number, 12)
        month = offset + 1
        month_end = monthrange(year, month)[1]
        dates.append(date(year, month, month_end if end_of_month else min(day_number, month_end)))
    if dates[-1] != dated:
        raise ValueError("Coupon dated date must begin a regular semiannual period")
    return list(reversed(dates[:-1]))


@dataclass(frozen=True)
class TreasuryTerms:
    cusip: str
    kind: str
    dated_on: date
    issued_on: date
    maturity: date
    annual_coupon_rate: float
    face_increment_usd: int


@dataclass(frozen=True)
class TreasuryPayment:
    scheduled_on: date
    paid_on: date
    coupon_per_100: float
    principal_per_100: float


@dataclass(frozen=True)
class TreasuryQuote:
    observed_on: date
    clean_close_per_100: float


class TreasuryHistory:
    def __init__(self, contracts: Path, payments: Path, quotes: Path):
        self.terms: dict[str, TreasuryTerms] = {}
        self.payments: dict[str, list[TreasuryPayment]] = {}
        self.quotes: dict[str, list[TreasuryQuote]] = {}

        due_dates: dict[str, list[date]] = {}
        for row in _rows(contracts, {"cusip", "kind", "dated_on", "issued_on", "maturity",
                                    "annual_coupon_rate", "face_increment_usd"}):
            cusip = row["cusip"].strip().upper()
            terms = TreasuryTerms(
                cusip, row["kind"].strip().lower(), date.fromisoformat(row["dated_on"]),
                date.fromisoformat(row["issued_on"]), date.fromisoformat(row["maturity"]),
                float(row["annual_coupon_rate"]), int(row["face_increment_usd"]),
            )
            if (not re.fullmatch(r"[0-9A-Z]{8}[0-9]", cusip) or cusip in self.terms
                    or terms.kind not in {"bill", "note", "bond"}
                    or not terms.dated_on <= terms.issued_on < terms.maturity
                    or not isfinite(terms.annual_coupon_rate) or not 0 <= terms.annual_coupon_rate < 1
                    or (terms.kind == "bill") != (terms.annual_coupon_rate == 0)
                    or terms.face_increment_usd <= 0):
                raise ValueError(f"Invalid or duplicate nominal Treasury contract {cusip}")
            self.terms[cusip] = terms
            due_dates[cusip] = ([terms.maturity] if terms.kind == "bill"
                                else _semiannual_dates(terms.maturity, terms.dated_on))
            if terms.issued_on >= due_dates[cusip][0]:
                raise ValueError(f"Use original issue terms for Treasury {cusip}")
        if not self.terms:
            raise ValueError("No Treasury contracts supplied")

        payment_dates: dict[str, dict[date, date]] = {cusip: {} for cusip in self.terms}
        for row in _rows(payments, {"cusip", "scheduled_on", "paid_on"}):
            cusip = row["cusip"].strip().upper()
            scheduled = date.fromisoformat(row["scheduled_on"])
            paid = date.fromisoformat(row["paid_on"])
            dates = payment_dates[cusip]
            if scheduled in dates or paid < scheduled or paid.weekday() >= 5:
                raise ValueError(f"Invalid or duplicate Treasury payment date for {cusip} on {scheduled}")
            dates[scheduled] = paid

        for cusip, terms in self.terms.items():
            recorded = payment_dates[cusip]
            if set(recorded) != set(due_dates[cusip]):
                raise ValueError(f"Expected complete regular Treasury payment schedule for {cusip}")
            ordered_payments = [recorded[day] for day in due_dates[cusip]]
            if ordered_payments != sorted(set(ordered_payments)):
                raise ValueError(f"Treasury payments must arrive in chronological order for {cusip}")
            self.payments[cusip] = [
                TreasuryPayment(day, recorded[day], 50 * terms.annual_coupon_rate,
                                100.0 if day == terms.maturity else 0.0)
                for day in due_dates[cusip]
            ]

        seen = set()
        for row in _rows(quotes, {"cusip", "observed_on", "clean_close_per_100"}):
            cusip = row["cusip"].strip().upper()
            terms = self.terms[cusip]
            observed = date.fromisoformat(row["observed_on"])
            clean = float(row["clean_close_per_100"])
            key = (cusip, observed)
            if (not terms.issued_on <= observed < terms.maturity or not isfinite(clean) or clean <= 0 or key in seen):
                raise ValueError(f"Invalid or duplicate Treasury clean close for {cusip} on {observed}")
            self.quotes.setdefault(cusip, []).append(TreasuryQuote(observed, clean))
            seen.add(key)
        for records in self.quotes.values():
            records.sort(key=lambda quote: quote.observed_on)

    def quote_at(self, cusip: str, observed: date, max_age_days: int) -> TreasuryQuote:
        records = self.quotes.get(cusip, ())
        index = bisect_right(records, observed, key=lambda quote: quote.observed_on) - 1
        if index < 0 or (observed - records[index].observed_on).days > max_age_days:
            raise ValueError(f"No fresh Treasury clean close for {cusip} at {observed}")
        return records[index]
