"""v0.4.12: offline multi-asset co-movement stress and risk-budget diagnostics.

All dollar values and scenario distributions are supplied assumptions.
This is NOT a real IBKR margin model, a data-trained VaR estimate,
a broker What-If, or an authorization to trade.
"""

from __future__ import annotations

from atlaspm.engine.contracts import validated_object

from atlaspm.engine.contracts import finite_number

from math import sqrt
from typing import Any, Mapping

TRADING_DAYS = 252
NORMAL_99_Z = 2.3263478740408408
DOLLAR_TOLERANCE = 0.01
ALLOWED_TYPES = {"equity", "treasury"}
MAX_ASSETS = 12
MAX_SCENARIOS = 20


def _obj(value: Any, field: str) -> dict[str, Any]:
    return validated_object(value, field, invalid_message=f"{field} must be an object")

def _num(value: Any, field: str, low: float | None = None,
         high: float | None = None) -> float:
    return finite_number(value, field, minimum=low, maximum=high,
    invalid_message=f"{field} must be a finite number",
    minimum_message=f"{field} below allowed range",
    maximum_message=f"{field} above allowed range")

def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an integer in [{minimum},{maximum}]")
    return value


def _correlation_matrix(matrix: Any, size: int, label: str) -> list[list[float]]:
    if (not isinstance(matrix, list) or len(matrix) != size or
            any(not isinstance(row, list) or len(row) != size for row in matrix)):
        raise ValueError(f"{label} must be a square matrix aligned to asset order")
    corr = [
        [_num(value, label, -1.0, 1.0) for value in row]
        for row in matrix
    ]
    for i in range(size):
        if abs(corr[i][i] - 1.0) > 1e-9:
            raise ValueError(f"{label} diagonal must equal one")
        for j in range(i):
            if abs(corr[i][j] - corr[j][i]) > 1e-9:
                raise ValueError(f"{label} must be symmetric")
    # Semidefinite-aware Cholesky: a zero pivot must have zero remaining
    # cross-covariance. This accepts valid perfectly correlated portfolios.
    factors = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1):
            residual = corr[i][j] - sum(
                factors[i][k] * factors[j][k] for k in range(j)
            )
            if i == j:
                if residual < -1e-8:
                    raise ValueError(f"{label} is not positive semidefinite")
                factors[i][i] = sqrt(max(0.0, residual))
            elif factors[j][j] > 1e-10:
                factors[i][j] = residual / factors[j][j]
            elif abs(residual) > 1e-8:
                raise ValueError(f"{label} is not positive semidefinite")
    return corr


def _one_day_var(values: list[float], annual_vols: list[float],
                 corr: list[list[float]], multiplier: float) -> float:
    annual_var = sum(
        values[i] * values[j] * annual_vols[i] * annual_vols[j] * corr[i][j]
        for i in range(len(values)) for j in range(len(values))
    )
    if annual_var < -1e-5:
        raise ValueError("Computed negative variance: invalid correlation inputs")
    return NORMAL_99_Z * sqrt(max(0.0, annual_var) / TRADING_DAYS) * multiplier


def analyze_multiasset_risk_budget(
    stress_data: Mapping[str, Any], assumptions_data: Mapping[str, Any],
) -> dict[str, Any]:
    """Scenario co-shocks + covariance VaR from explicitly supplied risk sleeves.

    A saved prior report is not authenticated; this function only compares
    its self-reported schema, blocked states and internal arithmetic.
    """
    upstream = _obj(stress_data, "upstream stress report")
    conf = _obj(assumptions_data, "multiasset assumptions")
    if (upstream.get("method") != "atlaspm_offline_unverified_margin_stress" or
            upstream.get("status") != "HYPOTHETICAL_STRESS_ONLY"):
        raise ValueError("Expected a v0.4.11 hypothetical stress report")
    account = upstream.get("account_id")
    if not isinstance(account, str) or not account:
        raise ValueError("Missing prior account identity")
    for name in (
        "live_execution_enabled", "ready_for_institutional_risk_gate",
        "IBKR_financing_debt_separately_verified", "broker_treasury_collateral_verified",
        "IBKR_order_bound_whatif_verified",
    ):
        if upstream.get(name) is not False:
            raise ValueError("Unverified upstream report contains an unsupported approval claim")
    if conf.get("method") != "hypothetical_multiasset_risk_budget":
        raise ValueError("Invalid risk budget method")

    base = _obj(upstream.get("baseline_margin_usd_claim"), "baseline")
    nlv = _num(base.get("net_liquidation"), "net_liquidation", 0.01)
    elv = _num(base.get("equity_with_loan_value"), "equity_with_loan_value", 0)
    init = _num(base.get("initial_margin"), "initial_margin", 0)
    maint = _num(base.get("maintenance_margin"), "maintenance_margin", 0)
    available = _num(base.get("available_funds"), "available_funds")
    excess = _num(base.get("excess_liquidity"), "excess_liquidity")
    if abs(available - (elv - init)) > 1 or abs(excess - (elv - maint)) > 1:
        raise ValueError("Baseline IBKR margin fields are inconsistent")
    loan = _num(upstream.get("financing_principal_usd_claim"), "loan principal", 0)
    rate = _num(upstream.get("assumed_annual_financing_apr"), "assumed APR", 0, 1)
    shockable = _num(upstream.get("assumed_shockable_position_value_usd"),
                     "upstream shockable notional", 0)
    _num(upstream.get("existing_accrued_interest_usd_claim_not_double_counted"),
         "existing accrued interest", 0)

    limits = _obj(conf.get("risk_limits"), "risk_limits")
    max_var = _num(limits.get("max_one_day_99pct_var_fraction_of_nlv"), "VaR limit", 0, 2)
    max_gross = _num(limits.get("max_gross_exposure_to_nlv"), "gross limit", 0.01, 20)
    min_buffer = _num(limits.get("min_excess_liquidity_to_nlv"), "buffer limit", 0, 1)
    max_interest = _num(limits.get("max_assumed_annual_interest_to_nlv"),
                        "annual interest limit", 0, 2)

    rows = conf.get("assets")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ASSETS:
        raise ValueError("Require between 1 and 12 hypothetical asset sleeves")
    ids: list[str] = []
    values: list[float] = []
    vols: list[float] = []
    classes: list[str] = []
    for item in rows:
        asset = _obj(item, "asset")
        identifier = asset.get("id")
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 60
                or identifier in ids):
            raise ValueError("Asset IDs must be unique and nonempty")
        kind = asset.get("type")
        if kind not in ALLOWED_TYPES:
            raise ValueError("Only hypothetical long equity and Treasury sleeves supported")
        ids.append(identifier)
        classes.append(kind)
        values.append(_num(asset.get("market_value_usd"), "asset market value", 0))
        vols.append(_num(asset.get("annual_volatility"), "annual volatility", 0, 4))
    total = sum(values)
    if abs(total - shockable) > DOLLAR_TOLERANCE:
        raise ValueError("Asset sleeve values must match prior assumed shockable notional")
    if total <= 0:
        raise ValueError("Shockable portfolio must have a positive value")
    base_corr = _correlation_matrix(conf.get("base_correlation"), len(ids), "base_correlation")
    cases = conf.get("scenarios")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_SCENARIOS:
        raise ValueError("Require 1 to 20 stress scenarios")
    names: set[str] = set()
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        row = _obj(case, "scenario")
        name = row.get("name")
        if (not isinstance(name, str) or not name or len(name) > 80
                or name in names):
            raise ValueError("Scenario name must be unique and nonempty")
        names.add(name)
        shock_obj = _obj(row.get("asset_returns"), "scenario asset_returns")
        if set(shock_obj) != set(ids):
            raise ValueError("Scenario must supply exactly one return per asset ID")
        returns = [_num(shock_obj[asset], "asset return", -1, 1) for asset in ids]
        corr = _correlation_matrix(
            row.get("correlation_override", base_corr), len(ids), "scenario correlation"
        )
        vol_multiplier = _num(row.get("volatility_multiplier", 1),
                              "volatility multiplier", 0.1, 10)
        maint_multiplier = _num(row.get("maintenance_multiplier", 1),
                                "maintenance multiplier", 1, 5)
        days = _integer(row.get("horizon_days"), "horizon_days", 0, 365)
        apr_add = _num(row.get("apr_additive", 0), "apr_additive", 0, 1)
        assumed_apr = rate + apr_add
        if assumed_apr > 1:
            raise ValueError("Combined hypothetical APR cannot exceed 100%")
        signed_mark_pnl = sum(v * r for v, r in zip(values, returns))
        new_interest = loan * assumed_apr * days / 365
        modeled_nlv = nlv + signed_mark_pnl - new_interest
        modeled_elv = elv + signed_mark_pnl - new_interest
        modeled_maint = maint * maint_multiplier
        modeled_init = init * maint_multiplier
        modeled_excess = modeled_elv - modeled_maint
        modeled_available = modeled_elv - modeled_init
        modeled_buffer = modeled_excess / modeled_nlv if modeled_nlv > 0 else None
        one_day_var = _one_day_var(values, vols, corr, vol_multiplier)
        var_frac = one_day_var / nlv
        gross_frac = total / nlv
        annual_interest_frac = loan * assumed_apr / nlv
        issues: list[str] = []
        if modeled_nlv <= 0:
            issues.append("PROJECTED_NONPOSITIVE_NLV")
        if modeled_excess <= 0:
            issues.append("PROJECTED_MAINTENANCE_DEFICIT")
        if modeled_available < 0:
            issues.append("PROJECTED_NEGATIVE_AVAILABLE_FUNDS")
        if modeled_buffer is not None and modeled_buffer < min_buffer:
            issues.append("PROJECTED_BUFFER_BELOW_POLICY_FLOOR")
        if var_frac > max_var + 1e-12:
            issues.append("PARAMETRIC_VAR_BUDGET_BREACH")
        if gross_frac > max_gross + 1e-12:
            issues.append("GROSS_EXPOSURE_BUDGET_BREACH")
        if annual_interest_frac > max_interest + 1e-12:
            issues.append("ASSUMED_FINANCING_COST_BUDGET_BREACH")
        state = (
            "HYPOTHETICAL_DEFICIT" if modeled_nlv <= 0 or modeled_excess <= 0
            else "HYPOTHETICAL_LIMIT_BREACH" if issues
            else "HYPOTHETICAL_WITHIN_LIMITS"
        )
        results.append({
            "index": index,
            "private_name": name,
            "state": state,
            "issues": sorted(issues),
            "asset_returns": {asset: ret for asset, ret in zip(ids, returns)},
            "correlation_matrix_assumption": corr,
            "volatility_multiplier": vol_multiplier,
            "horizon_days": days,
            "assumed_apr": assumed_apr,
            "assumed_signed_market_pnl_usd": signed_mark_pnl,
            "new_simple_financing_interest_usd": new_interest,
            "hypothetical_nlv_usd": modeled_nlv,
            "hypothetical_equity_with_loan_value_usd": modeled_elv,
            "hypothetical_maintenance_requirement_usd": modeled_maint,
            "hypothetical_initial_requirement_usd": modeled_init,
            "hypothetical_excess_liquidity_usd": modeled_excess,
            "hypothetical_available_funds_usd": modeled_available,
            "hypothetical_excess_to_nlv": modeled_buffer,
            "one_day_99pct_delta_normal_var_usd": one_day_var,
            "one_day_99pct_delta_normal_var_to_starting_nlv": var_frac,
            "assumed_gross_exposure_to_starting_nlv": gross_frac,
            "assumed_annual_interest_to_starting_nlv": annual_interest_frac,
            "hypothetical_limits_satisfied": len(issues) == 0,
        })
    return {
        "method": "atlaspm_offline_multiasset_correlation_budget",
        "status": "HYPOTHETICAL_RISK_BUDGET_ONLY",
        "account_id": account,
        "upstream_stress_source": "unsigned_local_diagnostic",
        "asset_sleeve_count": len(ids),
        "asset_sleeves": [
            {"id": id_, "type": typ, "value_usd_assumption": v,
             "annual_volatility_assumption": vol}
            for id_, typ, v, vol in zip(ids, classes, values, vols)
        ],
        "starting_nlv_usd_claim": nlv,
        "starting_equity_with_loan_value_usd_claim": elv,
        "starting_maintenance_usd_claim": maint,
        "financing_principal_usd_claim": loan,
        "total_shockable_value_usd_assumption": total,
        "risk_limits": limits,
        "base_correlation_assumption": base_corr,
        "scenarios": results,
        "IBKR_financing_debt_separately_verified": False,
        "broker_treasury_collateral_verified": False,
        "IBKR_order_bound_whatif_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "All correlation, volatility, asset values, interest rates and stress returns are assumptions.",
            "Deterministic shock P&L uses supplied asset returns; correlation changes parametric risk only, not shock P&L.",
            "The parametric daily 99% VaR uses a zero-drift normal approximation, annual volatility / sqrt(252) and starting NLV; it is not a validated tail-risk estimate.",
            "This reuses unsigned, potentially stale local diagnostic files; no broker source verification or loan principal authentication occurred.",
            "The initial margin and maintenance multipliers are simplified static assumptions, not IBKR Portfolio Margin, security-specific haircuts or liquidation rules.",
            "Existing loan principal was not subtracted again from NLV; existing accrued interest was not charged again.",
            "The risk budget evaluates hypothetical constraints only; no trading, automated deleveraging or funding rights are granted.",
        ],
    }


def redacted_multiasset_summary(result: Mapping[str, Any], path: str) -> dict[str, Any]:
    account = str(result["account_id"])
    return {
        "written": path,
        "method": result["method"],
        "status": result["status"],
        "account_hint": "****" + account[-4:],
        "asset_sleeve_count": result["asset_sleeve_count"],
        "scenarios": [
            {"index": row["index"], "state": row["state"], "issues": row["issues"]}
            for row in result["scenarios"]
        ],
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Only scenario indices, states and reason codes shown; all asset IDs and amounts remain private.",
            "Statistical risk estimates use assumptions, not live IBKR margin approval.",
        ],
    }
