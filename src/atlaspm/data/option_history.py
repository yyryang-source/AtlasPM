"""Normalized historical option contracts, market quotes and settlement values."""

from __future__ import annotations

import csv
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo


_NEW_YORK = ZoneInfo("America/New_York")


def option_timestamp(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    if value.utcoffset() is None:
        raise ValueError("Option timestamps must include a UTC offset")
    return value.astimezone(UTC)


def _rows(path: Path, columns: tuple[str, ...]):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("Option CSV requires unique column names")
        if not set(columns).issubset(reader.fieldnames):
            raise ValueError("Option CSV needs columns: " + ",".join(columns))
        for number, record in enumerate(reader, 2):
            row = {name: (record[name] or "").strip() for name in columns}
            if not all(row.values()):
                raise ValueError(f"Missing option CSV value at row {number}")
            yield row


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    right: str
    strike: float
    last_trade_at: datetime
    expiration_at: datetime
    multiplier: int
    exercise_style: str
    settlement_type: str
    exercise_threshold_usd: float


@dataclass(frozen=True)
class OptionQuote:
    at: datetime
    bid: float
    ask: float
    bid_size: int
    ask_size: int


@dataclass(frozen=True)
class OptionSettlement:
    reference_at: datetime
    available_at: datetime
    cash_at: datetime
    settlement_value: float


def load_option_contracts(contracts: Path) -> dict[str, OptionContract]:
    catalog: dict[str, OptionContract] = {}
    contract_columns = ("contract_id", "underlying", "right", "strike", "last_trade_at", "expiration_at",
                        "multiplier", "exercise_style", "settlement_type", "exercise_threshold_usd")
    for row in _rows(contracts, contract_columns):
        key = row["contract_id"]
        if key in catalog:
            raise ValueError(f"Duplicate option contract {key}")
        contract = OptionContract(
            row["underlying"].upper(), row["right"].lower(), float(row["strike"]),
            option_timestamp(row["last_trade_at"]), option_timestamp(row["expiration_at"]),
            int(row["multiplier"]), row["exercise_style"].lower(), row["settlement_type"].lower(),
            float(row["exercise_threshold_usd"]),
        )
        if (contract.right not in {"call", "put"} or not isfinite(contract.strike) or contract.strike <= 0
                or contract.last_trade_at > contract.expiration_at or contract.multiplier < 1
                or contract.exercise_style not in {"american", "european"}
                or contract.settlement_type not in {"cash", "physical"}
                or not isfinite(contract.exercise_threshold_usd) or contract.exercise_threshold_usd < 0):
            raise ValueError(f"Invalid option contract terms for {key}")
        catalog[key] = contract
    if not catalog:
        raise ValueError("No option contracts supplied")
    return catalog


class OptionHistory:
    def __init__(self, contracts: Path, quotes: Path, settlements: Path):
        self.contracts = load_option_contracts(contracts)

        self.quotes: dict[str, list[OptionQuote]] = {}
        seen = set()
        for row in _rows(quotes, ("contract_id", "quote_at", "bid", "ask", "bid_size", "ask_size")):
            key = row["contract_id"]
            self._require_contract(key)
            quote = OptionQuote(option_timestamp(row["quote_at"]), float(row["bid"]), float(row["ask"]),
                                int(row["bid_size"]), int(row["ask_size"]))
            if (not isfinite(quote.bid) or not isfinite(quote.ask) or not 0 <= quote.bid <= quote.ask
                    or min(quote.bid_size, quote.ask_size) < 0 or quote.at > self.contracts[key].last_trade_at):
                raise ValueError(f"Invalid option bid, ask, size or timestamp for {key}")
            if (key, quote.at) in seen:
                raise ValueError(f"Duplicate option quote for {key} at {quote.at.isoformat()}")
            seen.add((key, quote.at))
            self.quotes.setdefault(key, []).append(quote)
        for records in self.quotes.values():
            records.sort(key=lambda quote: quote.at)

        self.settlements: dict[str, OptionSettlement] = {}
        for row in _rows(settlements, ("contract_id", "reference_at", "available_at", "cash_at", "settlement_value")):
            key = row["contract_id"]
            self._require_contract(key)
            if key in self.settlements:
                raise ValueError(f"Duplicate option settlement for {key}")
            settlement = OptionSettlement(
                option_timestamp(row["reference_at"]), option_timestamp(row["available_at"]),
                option_timestamp(row["cash_at"]), float(row["settlement_value"]),
            )
            contract = self.contracts[key]
            expiration = contract.expiration_at
            reference_day = settlement.reference_at.astimezone(_NEW_YORK).date()
            last_trade_day = contract.last_trade_at.astimezone(_NEW_YORK).date()
            if (not isfinite(settlement.settlement_value) or settlement.settlement_value < 0
                    or reference_day < last_trade_day
                    or settlement.reference_at > min(expiration, settlement.available_at)
                    or max(settlement.available_at, expiration) >= settlement.cash_at):
                raise ValueError(f"Invalid settlement value or chronology for {key}")
            self.settlements[key] = settlement

    def _require_contract(self, key: str) -> None:
        if key not in self.contracts:
            raise ValueError(f"Unknown option contract {key}")

    def quote_at(self, key: str, as_of: datetime, max_age: timedelta) -> OptionQuote:
        records = self.quotes.get(key, [])
        index = bisect_right(records, as_of, key=lambda quote: quote.at) - 1
        if index < 0 or as_of - records[index].at > max_age:
            raise ValueError(f"No fresh option quote for {key} at {as_of.isoformat()}")
        quote = records[index]
        if quote.at.astimezone(_NEW_YORK).date() != as_of.astimezone(_NEW_YORK).date() or quote.ask == 0:
            raise ValueError(f"No usable option quote for {key} at {as_of.isoformat()}")
        return quote
