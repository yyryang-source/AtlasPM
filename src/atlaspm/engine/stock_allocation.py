"""Single-period, cost-aware long-only allocation for a ranked stock cohort."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class StockAllocationSettings:
    lookback_sessions: int = 63
    max_weight: float = .55
    sector_cap: float | None = None
    turnover_limit: float = 2.0
    risk_aversion: float = 2.0
    rank_tilt: float = .02

    def __post_init__(self) -> None:
        if type(self.lookback_sessions) is not int or not 20 <= self.lookback_sessions <= 252:
            raise ValueError("Quant lookback must be 20..252 sessions")
        if not (0 < self.max_weight <= 1 and 0 <= self.turnover_limit <= 2):
            raise ValueError("Quant stock cap and turnover must be in (0, 1] and [0, 2]")
        if self.sector_cap is not None and not 0 < self.sector_cap <= 1:
            raise ValueError("Quant sector cap must be in (0, 1]")
        if not (isfinite(self.risk_aversion) and self.risk_aversion > 0 and 0 <= self.rank_tilt <= 1):
            raise ValueError("Quant risk aversion must be positive and rank tilt must be in [0, 1]")


@dataclass(frozen=True)
class StockAllocationSignal:
    symbols: tuple[str, ...]
    scores: tuple[float, ...]
    sectors: tuple[str | None, ...]
    annual_covariance: Any


def _scientific_stack():
    try:
        import numpy as np
        from scipy import optimize
    except ImportError as exc:
        raise ValueError("Install the portfolio extra: python -m pip install -e '.[portfolio]'") from exc
    return np, optimize


def prepare_stock_allocation(
    selected: list[dict[str, Any]],
    prices: dict[str, dict[date, float]],
    lookback: list[date],
    settings: StockAllocationSettings,
) -> StockAllocationSignal | None:
    np, _ = _scientific_stack()
    symbols = tuple(row["symbol"] for row in selected)
    if symbols and (len(lookback) != settings.lookback_sessions + 1
                    or any(day not in prices[symbol] for symbol in symbols for day in lookback)):
        return None
    if symbols:
        closes = np.array([[prices[symbol][day] for day in lookback] for symbol in symbols])
        daily = closes[:, 1:] / closes[:, :-1] - 1
        covariance = np.atleast_2d(np.cov(daily, ddof=1)) * 252
        covariance = .75 * covariance + .25 * np.diag(np.diag(covariance))
        if not np.all(np.isfinite(covariance)):
            raise ValueError("Non-finite Quant covariance")
    else:
        covariance = np.empty((0, 0))
    return StockAllocationSignal(symbols, tuple(row["score"] for row in selected),
                                 tuple(row["sector"] for row in selected), covariance)


def optimize_stock_allocation(
    signal: StockAllocationSignal,
    current: dict[str, float],
    tradable: set[str],
    settings: StockAllocationSettings,
    *,
    cost_rate: float,
    holding_sessions: int,
) -> tuple[dict[str, float] | None, str]:
    """Return target notionals as fractions of pre-trade liquid equity."""
    np, optimize = _scientific_stack()
    indices = [i for i, symbol in enumerate(signal.symbols) if symbol in tradable]
    symbols = [signal.symbols[i] for i in indices]
    forced_sales = sum(weight for symbol, weight in current.items() if symbol not in tradable)
    turnover_room = settings.turnover_limit - forced_sales
    if turnover_room < -1e-10:
        return None, "FORCED_EXITS_EXCEED_TURNOVER_LIMIT"
    if not symbols:
        return {}, "CASH_NO_TRADABLE_CANDIDATES"
    n = len(symbols)
    old = np.array([current.get(symbol, 0.0) for symbol in symbols])
    identity = np.eye(n)
    ones = np.ones(n)
    zeros = np.zeros(n)
    fees = cost_rate * ones
    remaining_budget = 1 - cost_rate * forced_sales

    # Variables are risky notionals x and per-name |x - old| upper bounds t.
    rows = [*np.hstack((identity, -identity)), *np.hstack((-identity, -identity))]
    bounds = [*old, *(-old)]
    rows.extend((np.r_[ones, fees], np.r_[zeros, ones]))
    bounds.extend((remaining_budget, max(0.0, turnover_room)))
    for asset in identity:
        rows.append(np.r_[asset, settings.max_weight * fees])
        bounds.append(settings.max_weight * remaining_budget)
    if settings.sector_cap is not None:
        sector_members = defaultdict(list)
        for position, index in enumerate(indices):
            sector = signal.sectors[index]
            if not sector:
                raise ValueError("Quant sector limit requires a known sector for each selected stock")
            sector_members[sector].append(position)
        for members in sector_members.values():
            rows.append(np.r_[identity[members].sum(axis=0), settings.sector_cap * fees])
            bounds.append(settings.sector_cap * remaining_budget)

    matrix = np.asarray(rows)
    limits = np.asarray(bounds)
    feasible = optimize.linprog(np.r_[zeros, ones], A_ub=matrix, b_ub=limits,
                                bounds=(0, 1), method="highs")
    if feasible.status == 2:
        return None, "CONCENTRATION_TURNOVER_CONFLICT"
    if not feasible.success:
        raise ValueError(f"Quant feasibility solver failed: {feasible.message}")

    scores = np.array([signal.scores[i] / 100 for i in indices])
    horizon = holding_sessions / 252.0
    covariance = signal.annual_covariance[np.ix_(indices, indices)] * horizon
    reward = horizon * (.20 + settings.rank_tilt * scores)
    penalty = (cost_rate + 1e-10) * ones

    def objective(z):
        weights, trades = z[:n], z[n:]
        return .5 * settings.risk_aversion * (weights @ covariance @ weights) - reward @ weights + penalty @ trades

    def gradient(z):
        return np.r_[settings.risk_aversion * (covariance @ z[:n]) - reward, penalty]

    result = optimize.minimize(
        objective, feasible.x, jac=gradient, method="SLSQP", bounds=[(0, 1)] * (2 * n),
        constraints=[optimize.LinearConstraint(matrix, -np.inf, limits)],
        options={"ftol": 1e-12, "maxiter": 300},
    )
    if not result.success or np.max(matrix @ result.x - limits) > 1e-8:
        raise ValueError(f"Quant allocation solver failed: {result.message}")
    risky = {symbol: float(weight) for symbol, weight in zip(symbols, result.x[:n]) if weight > 0}
    return risky, "OPTIMIZED"
