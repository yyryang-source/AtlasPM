"""Constrained, fully-invested long-only mean-variance research optimizer.

No broker rules or forecasts are inferred. Inputs are explicitly supplied
annualized estimates. This is deterministic optimization, not trained AI.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite, sqrt


def _real(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _capped_simplex(values: Sequence[float], cap: float) -> list[float]:
    """Euclidean projection onto {w: sum(w)=1, 0<=w<=cap}."""
    if not values or len(values) * cap < 1 - 1e-12:
        raise ValueError("Infeasible per-asset cap")
    low = min(values) - cap
    high = max(values)
    for _ in range(90):
        middle = (low + high) / 2
        total = sum(min(cap, max(0.0, v - middle)) for v in values)
        if total > 1:
            low = middle
        else:
            high = middle
    result = [min(cap, max(0.0, v - (low + high) / 2)) for v in values]
    # Correct microscopic floating point residual without violating the cap.
    residual = 1.0 - sum(result)
    if residual > 0:
        for i in range(len(result)):
            add = min(residual, cap - result[i])
            result[i] += add
            residual -= add
    else:
        for i in range(len(result)):
            removal = min(-residual, result[i])
            result[i] -= removal
            residual += removal
    return result


def _validate_covariance(
    covariance: Sequence[Sequence[float]], size: int
) -> list[list[float]]:
    if not isinstance(covariance, (list, tuple)) or len(covariance) != size:
        raise ValueError("covariance must be a square matrix matching symbols")
    rows: list[list[float]] = []
    for i, row in enumerate(covariance):
        if not isinstance(row, (list, tuple)) or len(row) != size:
            raise ValueError("covariance must be square")
        rows.append([_real(v, f"covariance[{i}]") for v in row])
    for i in range(size):
        if rows[i][i] <= 0:
            raise ValueError("covariance must have positive diagonal variances")
        for j in range(i):
            if abs(rows[i][j] - rows[j][i]) > 1e-9:
                raise ValueError("covariance must be symmetric")

    # Pivot-free semidefinite check via LDL^T, tolerating zero pivots.
    lower = [[0.0] * size for _ in range(size)]
    diagonal = [0.0] * size
    scale = max(abs(v) for row in rows for v in row)
    tolerance = max(1e-12, scale * 1e-10)
    for i in range(size):
        lower[i][i] = 1.0
        diag = rows[i][i] - sum(
            lower[i][k] * lower[i][k] * diagonal[k] for k in range(i)
        )
        if diag < -tolerance:
            raise ValueError("covariance must be positive semidefinite")
        diagonal[i] = max(0.0, diag)
        for j in range(i + 1, size):
            remaining = rows[j][i] - sum(
                lower[j][k] * lower[i][k] * diagonal[k] for k in range(i)
            )
            if diagonal[i] <= tolerance:
                if abs(remaining) > tolerance:
                    raise ValueError("covariance must be positive semidefinite")
                lower[j][i] = 0.0
            else:
                lower[j][i] = remaining / diagonal[i]
    return rows


def optimize_mean_variance(
    expected_returns: Mapping[str, float],
    covariance: Sequence[Sequence[float]],
    *,
    risk_aversion: float = 4.0,
    max_weight: float = 0.60,
    leverage: float = 1.0,
    annual_financing_rate: float = 0.065,
) -> dict[str, object]:
    """Maximize levered expected return - 0.5*gamma*annual variance.

    All securities are long; total risky-asset weights sum to one. leverage
    scales holdings versus account equity. Financing on leverage-1 is
    hypothetical, with no cash buffer, fees or broker margin validation.
    """
    symbols = list(expected_returns)
    if not symbols or any(not isinstance(s, str) or not s.strip() for s in symbols):
        raise ValueError("Provide nonempty symbol names")
    mu = [_real(expected_returns[s], f"return for {s}") for s in symbols]
    n = len(symbols)
    cov = _validate_covariance(covariance, n)
    gamma = _real(risk_aversion, "risk_aversion")
    cap = _real(max_weight, "max_weight")
    gross = _real(leverage, "leverage")
    apr = _real(annual_financing_rate, "annual_financing_rate")
    if gamma <= 0:
        raise ValueError("risk_aversion must be positive")
    if not 0 < cap <= 1 or n * cap < 1 - 1e-12:
        raise ValueError("max_weight is infeasible")
    if not 1 <= gross <= 4:
        raise ValueError("leverage must be between 1 and 4")
    if apr < 0:
        raise ValueError("annual_financing_rate must be nonnegative")

    weights = [1.0 / n] * n
    lip = gamma * gross * gross * max(
        sum(abs(item) for item in row) for row in cov
    )
    step = 1.0 / lip
    max_iterations = 20000
    converged = False
    for iteration in range(1, max_iterations + 1):
        gradient = [
            gross * mu[i] - gamma * gross * gross *
            sum(cov[i][j] * weights[j] for j in range(n))
            for i in range(n)
        ]
        updated = _capped_simplex(
            [weights[i] + step * gradient[i] for i in range(n)], cap
        )
        change = max(abs(a - b) for a, b in zip(updated, weights))
        weights = updated
        if change < 1e-11:
            converged = True
            break
    if not converged:
        raise ValueError("Optimizer did not converge: revise inputs")

    marginal = [
        sum(cov[i][j] * weights[j] for j in range(n))
        for i in range(n)
    ]
    variance = sum(weights[i] * marginal[i] for i in range(n))
    expected_gross = gross * sum(weights[i] * mu[i] for i in range(n))
    financing = max(0.0, gross - 1) * apr
    annual_variance = gross * gross * variance
    risk_contributions = [
        gross * gross * weights[i] * marginal[i] for i in range(n)
    ]
    return {
        "method": "constrained_mean_variance",
        "is_trained_ai": False,
        "symbols": symbols,
        "weights": dict(zip(symbols, weights)),
        "leverage_assumption": gross,
        "annual_financing_rate_assumption": apr,
        "expected_gross_return": expected_gross,
        "annual_financing_cost_fraction": financing,
        "expected_net_return": expected_gross - financing,
        "expected_annual_volatility": sqrt(max(annual_variance, 0.0)),
        "annual_variance": annual_variance,
        "variance_contributions": dict(zip(symbols, risk_contributions)),
        "risk_aversion": gamma,
        "max_weight": cap,
        "iterations": iteration,
        "disclaimer": (
            "Annualized returns and covariance are user assumptions, not "
            "forecasts. No taxes, slippage, cash drag, shorts, fees or "
            "broker margin eligibility are modeled. Not a trade signal."
        ),
    }
