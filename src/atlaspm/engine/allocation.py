"""Seven research optimizers behind one hard-constraint interface.

Reuse AtlasPM's existing covariance validator, inverse-volatility baseline,
and mean-variance estimator. SciPy supplies constrained optimization only.
No failed solve is silently replaced with equal weight.
"""
from __future__ import annotations

from collections import defaultdict
from math import sqrt
from typing import Any, Mapping, Sequence

from atlaspm.engine.mean_variance import _validate_covariance, optimize_mean_variance
from atlaspm.engine.optimizer import inverse_volatility_weights
from atlaspm.engine.contracts import Account, Candidate, finite

METHODS = frozenset({
    "equal_weight", "risk_parity", "minimum_variance", "mean_variance",
    "cvar", "maximum_diversification", "risk_budgeting",
})


def optimize_target(candidates: Sequence[Candidate], account: Account,
                    market: Mapping[str, Any], constraints: Mapping[str, Any]) -> dict[str, Any]:
    try:
        import numpy as np
        from scipy.optimize import linprog, minimize
    except ImportError as exc:
        raise ValueError("PORTFOLIO_OPTIMIZER_REQUIRES_PIP_INSTALL_[portfolio]") from exc
    method = constraints.get("method", "equal_weight")
    if method not in METHODS:
        raise ValueError(f"UNSUPPORTED_OPTIMIZER:{method}")
    symbols = [c.symbol for c in candidates]
    if market.get("symbols") != symbols:
        raise ValueError("COVARIANCE_SYMBOL_ORDER_MISMATCH")
    n = len(symbols)
    cov = np.asarray(_validate_covariance(market.get("covariance"), n), dtype=float)
    # The old covariance validator checks positive semidefiniteness.
    returns_raw = market.get("expected_returns", {})
    if not isinstance(returns_raw, dict) or set(returns_raw) != set(symbols):
        raise ValueError("EXPECTED_RETURN_COVERAGE_MISMATCH")
    mu = np.array([finite(returns_raw[s], f"mu.{s}") for s in symbols], dtype=float)
    cap = finite(constraints.get("max_name_weight", 0.6), "max_name_weight", minimum=0)
    cash_reserve = finite(constraints.get("min_cash_fraction", 0.1), "min_cash_fraction", minimum=0)
    exposure = finite(constraints.get("max_gross_exposure", 1.0), "max_gross_exposure", minimum=0)
    turnover_limit = finite(constraints.get("max_turnover", 2.0), "max_turnover", minimum=0)
    if cap > 1 or cash_reserve >= 1 or exposure > 4:
        raise ValueError("INVALID_PORTFOLIO_CONSTRAINT")
    # V1 cannot validate broker margin; risky investment remains cash-funded.
    target_total = min(exposure, 1 - cash_reserve)
    if target_total <= 0 or cap * n + 1e-9 < target_total:
        raise ValueError("INFEASIBLE_NAME_CAP_OR_CASH_TARGET")
    equity = account.net_liquidation
    held = {h.symbol: h for h in account.holdings}
    if held.keys() - set(symbols):
        raise ValueError("UNMODELED_EXISTING_HOLDING_CANNOT_BE_DISCARDED")
    current = np.array([held[s].shares * held[s].price / equity if s in held else 0.0
                        for s in symbols], dtype=float)
    industries = defaultdict(list)
    for i, candidate in enumerate(candidates):
        industries[candidate.sector].append(i)
    raw_limits = constraints.get("sector_limits", {})
    if not isinstance(raw_limits, dict):
        raise ValueError("sector_limits must be a mapping")
    if set(raw_limits) - set(industries):
        raise ValueError("UNKNOWN_SECTOR_LIMIT")
    sector_limits = {s: finite(raw_limits.get(s, 1.0), f"sector.{s}", minimum=0)
                     for s in industries}
    if any(v > 1 for v in sector_limits.values()):
        raise ValueError("SECTOR_LIMIT_EXCEEDS_ONE")
    if sum(min(sector_limits[s], cap * len(ids)) for s, ids in industries.items()) + 1e-9 < target_total:
        raise ValueError("INFEASIBLE_SECTOR_CAPS")

    target_budget = float(target_total)
    bounds = [(0., cap)] * n
    base = [{"type": "eq", "fun": lambda x: float(np.sum(x) - target_budget)}]
    for sector, ids in industries.items():
        limit = sector_limits[sector]
        base.append({"type": "ineq", "fun": lambda x, idx=tuple(ids), lim=limit:
                    float(lim - np.sum(x[list(idx)]))})
    # Absolute-value turnover is intentionally conservative: sum |new-old|
    # excludes the cash leg and is checked again after integer rounding.
    base.append({"type": "ineq", "fun": lambda x:
                float(turnover_limit - np.sum(np.abs(x - current)))})
    budget = np.array([1.0 / n] * n)
    sigma = np.sqrt(np.diag(cov))
    if method == "risk_parity":
        seed = inverse_volatility_weights(dict(zip(symbols, map(float, sigma))), max_weight=max(cap / target_total, 1/n))
        budget = np.array([seed[s] for s in symbols], dtype=float)
    elif method == "mean_variance":
        # Keep prior tested AtlasPM mean-variance implementation as initializer.
        seed = optimize_mean_variance(dict(zip(symbols, map(float, mu))),
                                     cov.tolist(), max_weight=max(cap / target_total, 1/n),
                                     risk_aversion=finite(constraints.get("risk_aversion", 4.0), "risk_aversion", minimum=0.00001))
        budget = np.array([seed["weights"][s] for s in symbols], dtype=float)
    x0 = budget * target_total
    budgets = constraints.get("risk_budgets", {})
    if method == "risk_budgeting":
        if not isinstance(budgets, dict) or set(budgets) != set(symbols):
            raise ValueError("RISK_BUDGET_COVERAGE_MISMATCH")
        b = np.array([finite(budgets[s], f"budget.{s}", minimum=0.000001) for s in symbols])
        b /= b.sum()
    else:
        b = np.ones(n) / n

    def variance(x: Any) -> float:
        return float(x @ cov @ x)

    def risk_loss(x: Any) -> float:
        contributions = x * (cov @ x)
        v = max(float(contributions.sum()), 1e-14)
        return float(np.sum((contributions / v - b) ** 2))

    if method == "cvar":
        scenarios = market.get("scenario_returns")
        if not isinstance(scenarios, list) or len(scenarios) < 20:
            raise ValueError("CVAR_REQUIRES_20_ALIGNED_PAST_SCENARIOS")
        matrix = np.asarray(scenarios, dtype=float)
        if matrix.shape != (len(scenarios), n) or not np.all(np.isfinite(matrix)):
            raise ValueError("INVALID_CVAR_SCENARIOS")
        confidence = finite(constraints.get("cvar_confidence", .95), "cvar_confidence")
        if not .5 < confidence < 1:
            raise ValueError("INVALID_CVAR_CONFIDENCE")
        m = len(matrix)
        # Variables w[n], VaR threshold, positive slacks[m], turnover slacks[n].
        dimension = 2*n + m + 1
        cost = np.zeros(dimension)
        cost[n] = 1.0
        cost[n+1:n+1+m] = 1. / ((1-confidence)*m)
        # Sparse block constraints avoid allocating O(m*m) zero coefficients.
        from scipy.sparse import csr_matrix, eye, hstack, vstack
        scenario_block = hstack([
            csr_matrix(-matrix), csr_matrix(-np.ones((m, 1))),
            -eye(m, format="csr"), csr_matrix((m, n))
        ], format="csr")
        ident = eye(n, format="csr")
        empty = csr_matrix((n, m+1))
        plus = hstack([ident, empty, -ident], format="csr")
        minus = hstack([-ident, empty, -ident], format="csr")
        turnover_row = hstack([csr_matrix((1, n+m+1)),
                               csr_matrix(np.ones((1, n)))], format="csr")
        sector_row = []
        sector_col = []
        for row_i, ids in enumerate(industries.values()):
            sector_row.extend([row_i]*len(ids))
            sector_col.extend(ids)
        sector_matrix = csr_matrix((np.ones(len(sector_col)), (sector_row, sector_col)),
                                   shape=(len(industries), n))
        sector_block = hstack([sector_matrix, csr_matrix((len(industries), m+n+1))],
                              format="csr")
        inequalities = vstack([scenario_block, plus, minus, turnover_row, sector_block],
                              format="csr")
        rhs = np.concatenate([np.zeros(m), current, -current, [turnover_limit],
                              [sector_limits[s] for s in industries]])
        eq = hstack([csr_matrix(np.ones((1, n))),
                     csr_matrix((1, dimension-n))], format="csr")
        lp_bounds = [(0., cap)]*n + [(None, None)] + [(0., None)]*(m+n)
        result = linprog(cost, A_ub=inequalities, b_ub=rhs,
                         A_eq=eq, b_eq=np.array([target_total]),
                         bounds=lp_bounds, method="highs")
        if not result.success:
            raise ValueError(f"OPTIMIZER_REFUSED:{method}:{result.message}")
        weights = result.x[:n]
        iterations = int(getattr(result, "nit", 0))
    else:
        if method == "equal_weight":
            objective = lambda x: float(np.sum((x - target_total / n)**2))
        elif method == "minimum_variance":
            objective = variance
        elif method == "mean_variance":
            gamma = float(constraints.get("risk_aversion", 4.0))
            objective = lambda x: .5*gamma*variance(x) - float(mu @ x)
        elif method == "maximum_diversification":
            objective = lambda x: -float(sigma @ x) / sqrt(max(variance(x), 1e-14))
        else:
            objective = risk_loss
        result = minimize(objective, x0=x0, method="SLSQP", bounds=bounds,
                          constraints=base, options={"maxiter": 4000, "ftol": 1e-11})
        if not result.success:
            raise ValueError(f"OPTIMIZER_REFUSED:{method}:{result.message}")
        weights = result.x
        iterations = int(result.nit)

    tol = 1e-6
    if (abs(sum(weights)-target_total) > tol
        or any(w < -tol or w > cap+tol for w in weights)
        or any(sum(weights[ids]) > sector_limits[s]+tol for s, ids in industries.items())
        or sum(abs(weights-current)) > turnover_limit+tol):
        raise ValueError("OPTIMIZER_CONSTRAINT_RESIDUAL_FAILED")
    modeled_variance = variance(weights)
    return {
        "method": method, "symbols": symbols,
        "weights": {s: float(w) for s, w in zip(symbols, weights)},
        "target_cash_fraction": 1-target_total, "iterations": iterations,
        "modeled_variance": modeled_variance,
        "modeled_annual_volatility": sqrt(max(0.,modeled_variance)),
        "turnover_fraction_conservative": float(sum(abs(weights-current))),
        "research_only": True, "live_execution_enabled": False,
        "broker_order_submission_enabled": False,
    }
