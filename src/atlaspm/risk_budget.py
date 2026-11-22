"""Pre-trade hypothetical risk-budget checks; read-only, never submits orders."""
from __future__ import annotations

from math import isfinite, sqrt
from typing import Any, Mapping, Sequence

from atlaspm.mean_variance import _validate_covariance


def _finite(value: object, label: str, *, lo: float = 0, hi: float | None = None, strict: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    f = float(value)
    if not isfinite(f) or f < lo or (strict and f == lo) or (hi is not None and f > hi):
        raise ValueError(f"{label} outside permitted range")
    return f


def check_risk_budget(
    weights: Mapping[str, float],
    covariance: Sequence[Sequence[float]],
    *,
    proposed_leverage: float = 2.0,
    max_allowed_leverage: float = 2.0,
    max_name_weight: float = 0.60,
    max_annual_volatility: float = 0.30,
    stress_decline: float = 0.30,
    max_stress_loss_fraction: float = 0.40,
    maintenance_fraction: float = 0.30,
    min_maintenance_excess_fraction: float = 0.05,
    annual_financing_rate: float = 0.065,
    max_annual_financing_cost_fraction: float = 0.08,
) -> dict[str, Any]:
    """Analyze risk limits on a long-only fully-invested cash/margin candidate.

    Covariance order must exactly match insertion order of weights.
    The stress is a common percentage loss on each risky asset. Results are
    scenario arithmetic only; real broker margin rules are not represented.
    """
    if not isinstance(weights, dict) or not weights:
        raise ValueError("weights must be a nonempty object in covariance symbol order")
    symbols = list(weights)
    if any(not isinstance(s, str) or not s.strip() for s in symbols):
        raise ValueError("Symbols must be nonempty strings")
    w = [_finite(weights[s], f"weight.{s}", hi=1) for s in symbols]
    if abs(sum(w) - 1.0) > 1e-8:
        raise ValueError("Weights must sum to 1 within 1e-8")
    cov = _validate_covariance(covariance, len(symbols))
    leverage = _finite(proposed_leverage, "proposed_leverage", lo=1, hi=4)
    lev_cap = _finite(max_allowed_leverage, "max_allowed_leverage", lo=1, hi=4)
    name_cap = _finite(max_name_weight, "max_name_weight", lo=0, hi=1, strict=True)
    vol_cap = _finite(max_annual_volatility, "max_annual_volatility", strict=True)
    shock = _finite(stress_decline, "stress_decline", hi=1)
    max_loss = _finite(max_stress_loss_fraction, "max_stress_loss_fraction", hi=1, strict=True)
    maintenance = _finite(maintenance_fraction, "maintenance_fraction", hi=1, strict=True)
    buffer = _finite(min_maintenance_excess_fraction, "min_maintenance_excess_fraction", hi=1)
    rate = _finite(annual_financing_rate, "annual_financing_rate")
    cost_cap = _finite(max_annual_financing_cost_fraction, "max_annual_financing_cost_fraction")

    variance = sum(w[i] * cov[i][j] * w[j] for i in range(len(w)) for j in range(len(w)))
    if variance < -1e-10:
        raise ValueError("Portfolio variance cannot be negative")
    annual_volatility = sqrt(max(0.0, variance))
    stressed_equity_fraction = 1.0 - leverage * shock
    stressed_maintenance_fraction = (leverage * maintenance * (1 - shock)) if leverage > 1 else 0.0
    stressed_excess = stressed_equity_fraction - stressed_maintenance_fraction
    annual_cost = max(0.0, leverage - 1.0) * rate
    concentration = max(w)

    limits = {
        "maximum_name_weight": concentration <= name_cap + 1e-10,
        "maximum_gross_leverage": leverage <= lev_cap + 1e-10,
        "maximum_modeled_annual_volatility": leverage * annual_volatility <= vol_cap + 1e-10,
        "maximum_uniform_stress_loss": leverage * shock <= max_loss + 1e-10,
        "minimum_stress_maintenance_excess": stressed_excess >= buffer - 1e-10,
        "maximum_annual_financing_cost": annual_cost <= cost_cap + 1e-10,
    }
    # These are upper bounds conditional on fully invested risky exposure.
    volatility_bound = vol_cap / annual_volatility if annual_volatility else float("inf")
    shock_bound = max_loss / shock if shock else float("inf")
    maintenance_denom = shock + maintenance * (1 - shock)
    maintenance_bound = (1 - buffer) / maintenance_denom if maintenance_denom else float("inf")
    interest_bound = 1.0 + cost_cap / rate if rate else float("inf")
    bounds = {
        "user_gross_leverage_cap": lev_cap,
        "volatility_cap": volatility_bound,
        "stress_loss_cap": shock_bound,
        "maintenance_buffer_cap_for_margin": maintenance_bound,
        "annual_interest_cost_cap": interest_bound,
    }
    cap = min(bounds.values())
    # A 1x unlevered account is NOT subject to maintenance requirements.
    if cap < 1 and all((name != "maintenance_buffer_cap_for_margin" or value >= 1) for name, value in bounds.items()):
        feasible = False
    else:
        feasible = all((value >= 1 - 1e-10) for name, value in bounds.items() if name != "maintenance_buffer_cap_for_margin") and concentration <= name_cap + 1e-10
    recommendation = max(1.0, min(cap, lev_cap)) if feasible else None
    return {
        "status": "WITHIN_RESEARCH_BUDGET" if all(limits.values()) else "EXCEEDS_RESEARCH_BUDGET",
        "passes_all_checks": all(limits.values()),
        "symbols_in_covariance_order": symbols,
        "weights": dict(zip(symbols, w)),
        "proposed_leverage": leverage,
        "estimated_unlevered_annual_volatility": annual_volatility,
        "estimated_levered_annual_volatility": leverage * annual_volatility,
        "uniform_stress_decline": shock,
        "stressed_equity_fraction_of_initial": stressed_equity_fraction,
        "stressed_maintenance_required_fraction_of_initial": stressed_maintenance_fraction,
        "stressed_maintenance_excess_fraction_of_initial": stressed_excess,
        "annual_financing_cost_fraction_of_initial": annual_cost,
        "individual_checks": limits,
        "failed_checks": [key for key, passed in limits.items() if not passed],
        "analytic_max_leverage_by_constraint": {k: (v if isfinite(v) else None) for k, v in bounds.items()},
        "conservative_gross_leverage_cap_under_these_assumptions": recommendation,
        "no_fully_invested_1x_solution_meets_all_limits": not feasible,
        "notes": [
            "This is a pre-trade research diagnostic, NOT broker-approved buying power or a trading instruction.",
            "Estimated volatility from historical covariance is not a forecast; correlated crashes may be worse.",
            "Stress is a single uniform close-to-close decline with constant maintenance and debt assumptions.",
            "Suggested leverage cap is only an analytic bound, not a verified risk-managed portfolio or a liquidation guarantee.",
            "A failed concentration check cannot be repaired by changing gross leverage alone.",
        ],
    }
