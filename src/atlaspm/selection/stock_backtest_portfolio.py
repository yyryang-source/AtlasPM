"""Cash and holdings shared by the stock strategy and equal-weight baseline."""

from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any

from atlaspm.selection.stock_delistings import StockDelisting


class StockBacktestPortfolio:
    def __init__(self, *, baseline: bool = False):
        self.cash = 1.0
        self.shares: dict[str, float] = {}
        self._marks: dict[str, float] = {}
        self._receivables: dict[str, tuple[float, StockDelisting]] = {}
        self.settlements: list[dict[str, Any]] = []
        self.turnover = 0.0
        self.cost = 0.0
        self._error_prefix = "MISSING_UNIVERSE_" if baseline else "MISSING_"

    @property
    def liquid_equity(self) -> float:
        return self.cash + sum(quantity * self._marks[symbol] for symbol, quantity in self.shares.items())

    @property
    def receivable_value(self) -> float:
        return sum(quantity * event.last_close for quantity, event in self._receivables.values())

    @property
    def equity(self) -> float:
        return self.liquid_equity + self.receivable_value

    def mark(
        self,
        observed: date,
        prices: dict[str, dict[date, float]],
        delistings: dict[str, StockDelisting],
    ) -> None:
        self._marks.clear()
        for symbol in list(self.shares):
            event = delistings.get(symbol)
            if event is not None and observed > event.last_trade_date:
                self._receivables[symbol] = (self.shares.pop(symbol), event)
                continue
            close = prices[symbol].get(observed)
            if close is None:
                raise ValueError(f"{self._error_prefix}HELD_PRICE: {symbol} {observed}; no forward fill")
            self._marks[symbol] = close
        if not isfinite(self.equity):
            raise ValueError(f"Invalid portfolio equity on {observed}")

    def rebalance(
        self,
        chosen: list[str],
        observed: date,
        prices: dict[str, dict[date, float]],
        delistings: dict[str, StockDelisting],
        cost_rate: float,
    ) -> tuple[float, float, list[str]]:
        liquid = self.liquid_equity
        if liquid == 0.0:
            return 0.0, 0.0, []
        unfilled: list[str] = []
        fraction = 1.0 / len(chosen) if chosen else 0.0
        target: dict[str, float] = {}
        for symbol in chosen:
            close = prices[symbol].get(observed)
            if close is None:
                event = delistings.get(symbol)
                if event is not None and event.last_trade_date < observed:
                    unfilled.append(symbol)
                    continue
                raise ValueError(f"{self._error_prefix}EXECUTION_PRICE: {symbol} {observed}")
            self._marks[symbol] = close
            target[symbol] = fraction

        current = {symbol: quantity * self._marks[symbol] / liquid for symbol, quantity in self.shares.items()}
        liquid_turnover = sum(abs(target.get(symbol, 0.0) - current.get(symbol, 0.0))
                              for symbol in sorted(current.keys() | target.keys()))
        fee = liquid * liquid_turnover * cost_rate
        if fee >= liquid:
            raise ValueError("Transaction cost exceeded liquid equity")
        turnover = liquid_turnover * (liquid / self.equity)
        after_fee = liquid - fee
        self.shares = {symbol: after_fee * weight / self._marks[symbol] for symbol, weight in target.items()}
        self.cash = after_fee - sum(quantity * self._marks[symbol] for symbol, quantity in self.shares.items())
        if self.cash < -1e-10:
            raise ValueError("Simulated holdings exceed liquid equity")
        self.cash = max(0.0, self.cash)
        self.turnover += turnover
        self.cost += fee
        return turnover, fee, unfilled

    def settle_delistings(self, observed: date) -> None:
        due = [symbol for symbol, (_, event) in self._receivables.items() if event.cash_date <= observed]
        for symbol in due:
            quantity, event = self._receivables.pop(symbol)
            carrying_value = quantity * event.last_close
            proceeds = carrying_value * (1 + event.return_from_last_close)
            if not isfinite(self.cash + proceeds):
                raise ValueError(f"Invalid delisting proceeds for {symbol} on {observed}")
            self.cash += proceeds
            self.settlements.append({
                "symbol": symbol,
                "last_trade_date": event.last_trade_date.isoformat(),
                "cash_date": event.cash_date.isoformat(),
                "booked_at_close": observed.isoformat(),
                "return_from_last_close": event.return_from_last_close,
                "carrying_value_index": carrying_value,
                "cash_proceeds_index": proceeds,
            })
        if due and not isfinite(self.equity):
            raise ValueError(f"Invalid portfolio equity after delisting settlements on {observed}")
