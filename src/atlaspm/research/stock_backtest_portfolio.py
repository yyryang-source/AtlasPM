"""Cash and holdings shared by the stock strategy and equal-weight baseline."""

from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any

from atlaspm.data.stock_delistings import StockDelisting


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

    @property
    def liquid_weights(self) -> dict[str, float]:
        liquid = self.liquid_equity
        return {symbol: quantity * self._marks[symbol] / liquid for symbol, quantity in self.shares.items()}

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

    def executable_symbols(
        self,
        chosen: list[str],
        observed: date,
        prices: dict[str, dict[date, float]],
        delistings: dict[str, StockDelisting],
    ) -> tuple[list[str], list[str]]:
        executable: list[str] = []
        unfilled: list[str] = []
        for symbol in chosen:
            close = prices[symbol].get(observed)
            if close is None:
                event = delistings.get(symbol)
                if event is not None and event.last_trade_date < observed:
                    unfilled.append(symbol)
                    continue
                raise ValueError(f"{self._error_prefix}EXECUTION_PRICE: {symbol} {observed}")
            self._marks[symbol] = close
            executable.append(symbol)
        return executable, unfilled

    def rebalance(
        self,
        chosen: list[str],
        observed: date,
        prices: dict[str, dict[date, float]],
        delistings: dict[str, StockDelisting],
        cost_rate: float,
        *,
        exact_costs: bool = False,
    ) -> tuple[float, float, list[str]]:
        liquid = self.liquid_equity
        if liquid == 0.0:
            return 0.0, 0.0, []
        executable, unfilled = self.executable_symbols(chosen, observed, prices, delistings)
        fraction = 1.0 / len(chosen) if chosen else 0.0
        target = dict.fromkeys(executable, fraction)

        current = self.liquid_weights
        if exact_costs:
            symbols = sorted(current.keys() | target.keys())
            # Solve remaining equity + the fee on final notionals = starting equity.
            lower, upper = 0.0, 1.0
            for _ in range(55):
                midpoint = (lower + upper) / 2
                turnover = sum(abs(midpoint * target.get(symbol, 0) - current.get(symbol, 0)) for symbol in symbols)
                if midpoint + cost_rate * turnover > 1:
                    upper = midpoint
                else:
                    lower = midpoint
            turnover, fee = self.execute_notionals({symbol: lower * weight for symbol, weight in target.items()}, cost_rate)
            return turnover, fee, unfilled
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

    def execute_notionals(self, target: dict[str, float], cost_rate: float) -> tuple[float, float]:
        """Execute absolute target notionals expressed in units of pre-trade liquid equity."""
        liquid = self.liquid_equity
        current = self.liquid_weights
        fraction_traded = sum(abs(target.get(symbol, 0.0) - current.get(symbol, 0.0))
                              for symbol in sorted(current.keys() | target.keys()))
        fee = liquid * fraction_traded * cost_rate
        cash = liquid - fee - liquid * sum(target.values())
        if cash < -1e-8 * liquid or any(not isfinite(weight) or weight < 0 for weight in target.values()):
            raise ValueError("Rebalance would exceed its liquid cash budget")
        turnover = fraction_traded * (liquid / self.equity)
        self.shares = {symbol: liquid * weight / self._marks[symbol] for symbol, weight in target.items() if weight > 0}
        self.cash = max(0.0, cash)
        self.turnover += turnover
        self.cost += fee
        return turnover, fee

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
