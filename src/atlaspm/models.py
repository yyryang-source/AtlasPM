"""Validated inputs for cash/margin, long-only portfolio analysis."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal


def nonnegative_number(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    if not isfinite(value) or value < 0:
        raise ValueError(f"{label} must be nonnegative and finite")
    return float(value)


@dataclass(frozen=True)
class Position:
    symbol: str
    market_value: float
    maintenance_requirement: float = 0.30

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("Position symbol must be nonempty")
        nonnegative_number(self.market_value, "position.market_value")
        fraction = nonnegative_number(
            self.maintenance_requirement, "position.maintenance_requirement"
        )
        if not 0 < fraction <= 1:
            raise ValueError("maintenance_requirement must be in (0, 1]")


@dataclass(frozen=True)
class PortfolioAccount:
    name: str
    account_type: Literal["cash", "margin"]
    cash: float
    debt: float
    positions: tuple[Position, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Account name must be nonempty")
        if self.account_type not in {"cash", "margin"}:
            raise ValueError("account_type must be 'cash' or 'margin'")
        nonnegative_number(self.cash, "cash")
        nonnegative_number(self.debt, "debt")
        if not isinstance(self.positions, tuple) or not all(
            isinstance(p, Position) for p in self.positions
        ):
            raise ValueError("positions must be a tuple of Position objects")
        if self.account_type == "cash" and self.debt != 0:
            raise ValueError("cash accounts cannot have margin debt")
        if self.equity <= 0:
            raise ValueError("Account equity must be greater than zero")

    @property
    def market_value(self) -> float:
        return sum(p.market_value for p in self.positions)

    @property
    def equity(self) -> float:
        return self.cash + self.market_value - self.debt

    @property
    def gross_leverage(self) -> float:
        return self.market_value / self.equity

    @property
    def maintenance_required(self) -> float:
        return sum(
            p.market_value * p.maintenance_requirement for p in self.positions
        )

    @property
    def maintenance_excess(self) -> float:
        return self.equity - self.maintenance_required

    def stress(self, decline: float) -> dict[str, float | bool]:
        """Uniform long-position loss; cash and debt unchanged."""
        fraction = nonnegative_number(decline, "decline")
        if fraction > 1:
            raise ValueError("decline must be between 0 and 1")
        stressed_market_value = self.market_value * (1 - fraction)
        stressed_equity = self.cash + stressed_market_value - self.debt
        stressed_maintenance = self.maintenance_required * (1 - fraction)
        excess = stressed_equity - stressed_maintenance
        return {
            "decline": fraction,
            "market_value": stressed_market_value,
            "equity": stressed_equity,
            "maintenance_required": stressed_maintenance,
            "maintenance_excess": excess,
            "maintenance_deficit": max(-excess, 0.0),
            "below_maintenance": excess < 0,
        }
