"""Offline IBKR margin stress with explicit assumptions and zero execution authority.

Uses previous *diagnostic* reports as inputs. Their numbers are unverified
claims or snapshots, not independently signed broker records. Never asserts a
broker liquidation threshold, a margin loan balance, or an order preflight.
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

USD_TOL = 1.0
FIELD_PAIR = {
    "net_liquidation": "NetLiquidation",
    "equity_with_loan_value": "EquityWithLoanValue",
    "initial_margin": "InitMarginReq",
    "maintenance_margin": "MaintMarginReq",
    "available_funds": "AvailableFunds",
    "excess_liquidity": "ExcessLiquidity",
}


def _obj(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _number(value: Any, label: str, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be finite numeric data")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{label} is below the allowed range")
    if maximum is not None and result > maximum:
        raise ValueError(f"{label} exceeds the allowed range")
    return result


def run_margin_stress(
    monitor_data: Mapping[str, Any],
    financing_data: Mapping[str, Any],
    assumptions_data: Mapping[str, Any],
) -> dict[str, Any]:
    """Compute static *illustrative* scenarios, never a broker margin forecast."""
    monitor = _obj(monitor_data, "monitor report")
    financing = _obj(financing_data, "financing report")
    assumptions = _obj(assumptions_data, "scenario assumptions")
    if monitor.get("method") != "ibkr_gateway_portfolio_summary_margin_monitor":
        raise ValueError("Unsupported margin monitor report")
    if financing.get("method") != "ibkr_offline_financing_margin_cross_check":
        raise ValueError("Unsupported financing report")
    if monitor.get("source") not in {
        "synthetic_ibkr_fixture", "local_authenticated_ibkr_gateway",
    }:
        raise ValueError("Unsupported margin monitor source")
    if financing.get("statement_source_claim") not in {
        "synthetic_margin_fixture", "manually_transcribed_broker_summary",
    }:
        raise ValueError("Unsupported financing source claim")
    if ((monitor["source"] == "synthetic_ibkr_fixture") !=
            (financing["statement_source_claim"] == "synthetic_margin_fixture")):
        raise ValueError("Synthetic and real input sources cannot be mixed")

    if monitor.get("monitor_state") not in {"NORMAL", "WATCH", "CRITICAL"}:
        raise ValueError("Monitor data is blocked or missing")
    if monitor.get("data_integrity_block_reasons") != []:
        raise ValueError("Monitor data has unresolved integrity issues")
    if financing.get("status") != "FIELDS_CONSISTENT_REVIEW_REQUIRED":
        raise ValueError("Financing claims did not reconcile")
    if financing.get("check_issues") != []:
        raise ValueError("Financing report has unresolved check issues")
    if financing.get("mapping_status") != "IDENTITIES_MAPPED_REVIEW_REQUIRED":
        raise ValueError("Financing mapping not reviewed")
    account = monitor.get("account_id")
    if not isinstance(account, str) or not account or financing.get("account_id") != account:
        raise ValueError("Input reports do not share one account identity")
    # Do not accept forged 'verified/approved' claims from a user-provided report.
    for report in (monitor, financing):
        if report.get("live_execution_enabled") is not False:
            raise ValueError("Upstream output cannot authorize execution")
        if report.get("ready_for_institutional_risk_gate") is not False:
            raise ValueError("Upstream output must remain outside the risk gate")
        if report.get("IBKR_financing_debt_separately_verified") is not False:
            raise ValueError("Upstream financing provenance is unsupported")

    margin = _obj(monitor.get("margin_summary_usd_claim"), "monitor margin")
    financing_margin = _obj(
        financing.get("margin_summary_claim_usd"), "financing margin"
    )
    baseline: dict[str, float] = {}
    for local_key, broker_key in FIELD_PAIR.items():
        current = _number(margin.get(local_key), f"monitor.{local_key}")
        separately_claimed = _number(financing_margin.get(broker_key), f"financing.{broker_key}")
        if abs(current - separately_claimed) > USD_TOL:
            raise ValueError("Two input reports disagree on the baseline margin")
        baseline[local_key] = current
    if baseline["net_liquidation"] <= 0 or baseline["equity_with_loan_value"] < 0:
        raise ValueError("Baseline account net liquidation/equity is invalid")
    if baseline["initial_margin"] < 0 or baseline["maintenance_margin"] < 0:
        raise ValueError("Baseline margin requirements cannot be negative")
    if abs(baseline["available_funds"] - (
        baseline["equity_with_loan_value"] - baseline["initial_margin"]
    )) > USD_TOL:
        raise ValueError("Available funds baseline does not reconcile")
    if abs(baseline["excess_liquidity"] - (
        baseline["equity_with_loan_value"] - baseline["maintenance_margin"]
    )) > USD_TOL:
        raise ValueError("Excess liquidity baseline does not reconcile")

    finance_claim = _obj(financing.get("financing_claim_usd"), "financing claim")
    loan = _number(
        finance_claim.get("loan_principal_from_statement"),
        "loan principal claim", minimum=0
    )
    loan_activity = _number(
        finance_claim.get("loan_principal_from_activity"),
        "loan activity claim", minimum=0
    )
    if abs(loan - loan_activity) > 0.01:
        raise ValueError("Financing loan claims mismatch")
    accrued_claim = _number(
        finance_claim.get("accrued_interest_from_statement"),
        "already accrued interest claim", minimum=0
    )
    # Accrued interest is reported separately; it may already be in NLV.
    # NEVER subtract it again as if it were a new expense.
    if assumptions.get("method") != "hypothetical_static_margin_stress":
        raise ValueError("Scenario input method unsupported")
    shockable = _number(
        assumptions.get("shockable_position_value_usd"),
        "shockable position value", minimum=0
    )
    interest_apr = _number(
        assumptions.get("assumed_annual_financing_apr"),
        "assumed annual financing APR", minimum=0, maximum=1
    )
    cushion_floor = _number(
        assumptions.get("minimum_excess_to_nlv_ratio"),
        "minimum buffer ratio", minimum=0, maximum=1
    )
    if shockable > 1e12:
        raise ValueError("Assumed shockable notional exceeds safety bounds")
    scenario_rows = assumptions.get("scenarios")
    if not isinstance(scenario_rows, list) or not 1 <= len(scenario_rows) <= 20:
        raise ValueError("Provide 1 to 20 scenario objects")

    results: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, entry in enumerate(scenario_rows, start=1):
        row = _obj(entry, "stress scenario")
        name = row.get("name")
        if (not isinstance(name, str) or not name or len(name) > 80
                or name in names):
            raise ValueError("Scenario names must be unique and <=80 characters")
        names.add(name)
        decline = _number(
            row.get("shock_fraction"), "shock_fraction", minimum=0, maximum=1
        )
        maint_multiplier = _number(
            row.get("maintenance_multiplier"), "maintenance_multiplier",
            minimum=1, maximum=5
        )
        days = row.get("horizon_days")
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 365:
            raise ValueError("horizon_days must be integer 0 through 365")

        price_loss = shockable * decline
        new_interest = loan * interest_apr * days / 365.0
        hypothetical_nlv = baseline["net_liquidation"] - price_loss - new_interest
        hypothetical_elv = baseline["equity_with_loan_value"] - price_loss - new_interest
        hypothetical_maintenance = baseline["maintenance_margin"] * maint_multiplier
        hypothetical_initial = baseline["initial_margin"] * maint_multiplier
        hypothetical_excess = hypothetical_elv - hypothetical_maintenance
        hypothetical_available = hypothetical_elv - hypothetical_initial
        ratio = hypothetical_excess / hypothetical_nlv if hypothetical_nlv > 0 else None
        issues: list[str] = []
        if hypothetical_nlv <= 0:
            issues.append("PROJECTED_NONPOSITIVE_EQUITY")
        if hypothetical_excess <= 0:
            issues.append("PROJECTED_MAINTENANCE_DEFICIT")
        if hypothetical_available < 0:
            issues.append("PROJECTED_NEGATIVE_AVAILABLE_FUNDS")
        if ratio is not None and ratio < cushion_floor:
            issues.append("PROJECTED_BUFFER_BELOW_POLICY_FLOOR")
        state = (
            "ILLUSTRATIVE_DEFICIT" if hypothetical_nlv <= 0 or hypothetical_excess <= 0
            else "ILLUSTRATIVE_WATCH" if issues else "ILLUSTRATIVE_ABOVE_FLOOR"
        )
        results.append({
            "index": index,
            "name": name,  # private report only: never print labels from input
            "scenario_state": state,
            "scenario_issues": sorted(issues),
            "assumed_shock_fraction": decline,
            "assumed_maintenance_multiplier": maint_multiplier,
            "horizon_days": days,
            "hypothetical_market_loss_usd": price_loss,
            "new_simple_financing_interest_usd": new_interest,
            "hypothetical_nlv_usd": hypothetical_nlv,
            "hypothetical_equity_with_loan_value_usd": hypothetical_elv,
            "hypothetical_initial_margin_usd": hypothetical_initial,
            "hypothetical_maintenance_margin_usd": hypothetical_maintenance,
            "hypothetical_available_funds_usd": hypothetical_available,
            "hypothetical_excess_liquidity_usd": hypothetical_excess,
            "hypothetical_excess_to_nlv_ratio": ratio,
        })

    return {
        "method": "atlaspm_offline_unverified_margin_stress",
        "status": "HYPOTHETICAL_STRESS_ONLY",
        "account_id": account,
        "upstream_monitor_state_claim": monitor["monitor_state"],
        "baseline_margin_usd_claim": baseline,
        "financing_principal_usd_claim": loan,
        "existing_accrued_interest_usd_claim_not_double_counted": accrued_claim,
        "assumed_shockable_position_value_usd": shockable,
        "assumed_annual_financing_apr": interest_apr,
        "minimum_excess_to_nlv_ratio": cushion_floor,
        "scenarios": results,
        "IBKR_financing_debt_separately_verified": False,
        "broker_treasury_collateral_verified": False,
        "IBKR_order_bound_whatif_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Prior JSON reports are user-controlled and not signed broker evidence.",
            "This is a static what-if scenario, NOT an IBKR What-If order preview, house-margin model, or liquidation forecast.",
            "Market loss is applied to an explicitly assumed shockable value; other assets and correlations are not modeled.",
            "Simple interest uses an assumed APR and claimed loan principal; no IBKR tiers or accrual conventions are verified.",
            "Existing accrued interest is already treated as part of snapshot accounting, not subtracted again.",
            "Neither signed settled cash nor USD.interest establishes financing principal or accrued debt.",
            "Saved upstream timestamps are not reauthenticated or refreshed; rerun captures for current observations.",
        ],
    }


def redacted_stress_console(report: Mapping[str, Any], path: str) -> dict[str, Any]:
    account = str(report["account_id"])
    return {
        "written": path,
        "method": report["method"],
        "status": report["status"],
        "account_hint": "****" + account[-4:],
        "scenarios": [
            {
                "index": x["index"],
                "scenario_state": x["scenario_state"],
                "scenario_issues": x["scenario_issues"],
            }
            for x in report["scenarios"]
        ],
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "All amounts, scenario labels and private account data remain in local_data/.",
            "Hypothetical risk diagnostics only; no broker trade approval or liquidation prediction.",
        ],
    }
