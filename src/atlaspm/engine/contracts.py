"""Point-in-time synthetic portfolio contracts; never broker authorization."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Mapping


def finite(value: object, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{field}: finite number required")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ValueError(f"{field}: must be >= {minimum}")
    return number


def timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field}: ISO-8601 timestamp required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}: invalid ISO-8601 timestamp") from exc
    if result.utcoffset() is None:
        raise ValueError(f"{field}: timezone offset is required")
    return result


def object_map(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field}: JSON object required")
    return value


@dataclass(frozen=True)
class Candidate:
    symbol: str
    security_id: str
    sector: str
    score: float
    rank: int
    version: str

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any], *, as_of: datetime,
                     max_age_seconds: float) -> "Candidate":
        symbol = raw.get("symbol")
        security_id = raw.get("security_id")
        sector = raw.get("sector")
        version = raw.get("model_version")
        if not all(isinstance(s, str) and s.strip() for s in (symbol, security_id, sector, version)):
            raise ValueError("SIGNAL_INCOMPLETE_IDENTITY_OR_MODEL")
        # Synthetic IDs are NOT a real ticker-to-listing or issuer mapping.
        if raw.get("identity_source") != "synthetic" or security_id != "SYN:" + symbol:
            raise ValueError(f"SECURITY_IDENTITY_UNVERIFIED:{symbol}")
        if raw.get("data_source") != "synthetic" or raw.get("data_quality") != "synthetic_test_only":
            raise ValueError(f"DATA_PROVENANCE_UNVERIFIED:{symbol}")
        if raw.get("research_eligible") is not True:
            raise ValueError(f"RESEARCH_INELIGIBLE:{symbol}")
        # No training approvals can be promoted through a supplied boolean.
        if raw.get("model_approved_for_training") is not False:
            raise ValueError(f"MODEL_PERMISSION_CLAIM_NOT_SUPPORTED:{symbol}")
        gen = timestamp(raw.get("generated_at"), "generated_at")
        available = timestamp(raw.get("available_at"), "available_at")
        data_available = timestamp(raw.get("data_available_at"), "data_available_at")
        expires = timestamp(raw.get("expires_at"), "expires_at")
        if not (data_available <= gen <= available <= as_of <= expires):
            raise ValueError(f"LOOKAHEAD_OR_EXPIRED_SIGNAL:{symbol}")
        if (as_of - data_available).total_seconds() > max_age_seconds:
            raise ValueError(f"STALE_INPUT_DATA:{symbol}")
        score = finite(raw.get("factor_score"), "factor_score")
        rank = raw.get("model_rank")
        if isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0:
            raise ValueError(f"INVALID_MODEL_RANK:{symbol}")
        return cls(symbol, security_id, sector, score, rank, version)


@dataclass(frozen=True)
class Holding:
    symbol: str
    shares: int
    price: float
    sector: str

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> "Holding":
        symbol = value.get("symbol")
        sector = value.get("sector")
        qty = value.get("shares")
        if not isinstance(symbol, str) or not symbol.strip() or not isinstance(sector, str) or not sector.strip():
            raise ValueError("INVALID_HOLDING_IDENTITY")
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise ValueError(f"INVALID_HOLDING_SHARES:{symbol}")
        mark_age = finite(value.get("mark_age_seconds", 0), "mark_age_seconds", minimum=0)
        if mark_age > 120 or value.get("mark_source", "synthetic") != "synthetic":
            raise ValueError(f"STALE_OR_UNVERIFIED_HOLDING_MARK:{symbol}")
        return cls(symbol, qty, finite(value.get("mark_price"), "mark_price", minimum=0.000001), sector)


@dataclass(frozen=True)
class Account:
    account_id: str
    kind: str
    cash: float
    debt: float
    accrued_interest: float
    holdings: tuple[Holding, ...]

    @property
    def net_liquidation(self) -> float:
        return self.cash + sum(h.shares * h.price for h in self.holdings) - self.debt - self.accrued_interest

    @property
    def market_value(self) -> float:
        return sum(h.shares * h.price for h in self.holdings)

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> "Account":
        account_id = value.get("account_id")
        kind = value.get("account_type")
        if not isinstance(account_id, str) or not account_id.strip() or kind not in {"cash", "margin"}:
            raise ValueError("INVALID_ACCOUNT_ID_OR_TYPE")
        if value.get("currency", "USD") != "USD":
            raise ValueError("V1_ACCOUNT_USD_ONLY")
        for name in ("unsettled_receivable", "unsettled_payable"):
            if finite(value.get(name, 0), name, minimum=0) > 0:
                raise ValueError("UNSETTLED_ACCOUNT_BALANCES_REQUIRE_FULL_LEDGER_ADAPTER")
        positions = value.get("positions")
        if not isinstance(positions, list):
            raise ValueError("positions: expected list")
        holdings = tuple(Holding.from_payload(object_map(p, "position")) for p in positions)
        if len({h.symbol for h in holdings}) != len(holdings):
            raise ValueError("DUPLICATE_ACCOUNT_HOLDINGS")
        cash = finite(value.get("settled_cash"), "settled_cash", minimum=0)
        debt = finite(value.get("margin_debt", 0), "margin_debt", minimum=0)
        accrued = finite(value.get("accrued_financing_interest", 0), "accrued_financing_interest", minimum=0)
        if kind == "cash" and (debt or accrued):
            raise ValueError("CASH_ACCOUNT_CANNOT_BORROW")
        result = cls(account_id, kind, cash, debt, accrued, holdings)
        if result.net_liquidation <= 0:
            raise ValueError("NONPOSITIVE_ACCOUNT_EQUITY")
        return result


def validate_candidates(payload: object, *, as_of: datetime,
                        max_age_seconds: float) -> list[Candidate]:
    if not isinstance(payload, list) or not 1 <= len(payload) <= 20:
        raise ValueError("SIGNALS_REQUIRE_1_TO_20_CANDIDATES")
    candidates = [Candidate.from_payload(object_map(s, "signal"), as_of=as_of,
                                         max_age_seconds=max_age_seconds) for s in payload]
    if len({s.symbol for s in candidates}) != len(candidates):
        raise ValueError("DUPLICATE_SECURITY_SIGNAL")
    if len({s.rank for s in candidates}) != len(candidates):
        raise ValueError("DUPLICATE_MODEL_RANK")
    return candidates
