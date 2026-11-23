"""v0.4.13: hypothetical reverse-stress thresholds from unsigned risk reports.

No live IBKR API calls, actual securities margin model, or order authority.
The derived shock boundaries are *assumptions*, not liquidation prices.
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

MAX_ASSETS = 12
MAX_CASES = 20
SUM_TOLERANCE_USD = 0.01


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _number(value: Any, name: str, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} requires finite numeric data")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} below range")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} above range")
    return number


def _boundary(initial: float, loss_per_unit_decline: float,
              cap: float) -> float | None:
    """First decline within the scanned interval reaching zero.

    A return of 0 means already at/through the mathematical threshold.
    A return of None means no crossing within the requested shock range.
    """
    if initial <= 0:
        return 0.0
    if loss_per_unit_decline <= 0:
        return None
    crossing = initial / loss_per_unit_decline
    if crossing > cap + 1e-12:
        return None
    return min(cap, max(0.0, crossing))


def reverse_margin_stress(
    upstream_data: Mapping[str, Any],
    case_data: Mapping[str, Any],
) -> dict[str, Any]:
    """Analytically solve simplified drawdown levels for selected risk limits.

    An accepted report is still a self-asserted, unsigned local file. Its
    numbers must never become IBKR buying power or an executable risk permit.
    """
    previous = _object(upstream_data, "v0.4.12 report")
    config = _object(case_data, "reverse-stress assumptions")
    if (previous.get("method") != "atlaspm_offline_multiasset_correlation_budget"
            or previous.get("status") != "HYPOTHETICAL_RISK_BUDGET_ONLY"):
        raise ValueError("Expected v0.4.12 offline multiasset risk output")
    if config.get("method") != "hypothetical_reverse_margin_stress":
        raise ValueError("Unsupported reverse-stress input method")
    account_id = previous.get("account_id")
    if not isinstance(account_id, str) or not account_id:
        raise ValueError("Account ID missing from upstream report")
    for field in (
        "live_execution_enabled", "ready_for_institutional_risk_gate",
        "IBKR_financing_debt_separately_verified",
        "broker_treasury_collateral_verified", "IBKR_order_bound_whatif_verified",
    ):
        if previous.get(field) is not False:
            raise ValueError("Unsupported upstream broker authorization/provenance claim")

    nlv = _number(previous.get("starting_nlv_usd_claim"), "NLV", 0.01)
    elv = _number(previous.get("starting_equity_with_loan_value_usd_claim"), "ELV", 0)
    maint = _number(previous.get("starting_maintenance_usd_claim"), "maintenance", 0)
    debt = _number(previous.get("financing_principal_usd_claim"), "financing claim", 0)
    total = _number(previous.get("total_shockable_value_usd_assumption"),
                    "total shockable value", 0)
    if total <= 0:
        raise ValueError("Total assumed shockable value must be positive")
    limits = _object(previous.get("risk_limits"), "prior risk limits")
    initial_interest_limit = _number(
        limits.get("max_assumed_annual_interest_to_nlv"), "interest budget", 0, 2
    )

    rows = previous.get("asset_sleeves")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ASSETS:
        raise ValueError("Require 1 to 12 prior asset sleeves")
    if previous.get("asset_sleeve_count") != len(rows):
        raise ValueError("Prior sleeve count does not match stored assets")
    ids: list[str] = []
    values: list[float] = []
    for record in rows:
        asset = _object(record, "prior asset sleeve")
        symbol = asset.get("id")
        if (not isinstance(symbol, str) or not symbol or len(symbol) > 60
                or symbol in ids):
            raise ValueError("Prior asset identifiers are missing or duplicated")
        if asset.get("type") not in {"equity", "treasury"}:
            raise ValueError("Unsupported prior asset class")
        ids.append(symbol)
        values.append(_number(asset.get("value_usd_assumption"), "prior asset value", 0))
    if abs(sum(values) - total) > SUM_TOLERANCE_USD:
        raise ValueError("Prior asset values do not reconcile with shockable notional")

    policy_floor = _number(config.get("min_excess_to_nlv_ratio"), "policy buffer", 0, 0.999999)
    maximum_drawdown = _number(
        config.get("maximum_drawdown_fraction"), "maximum drawdown", 0, 1
    )
    if maximum_drawdown <= 0:
        raise ValueError("maximum_drawdown_fraction must be positive")
    cases = config.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("Require 1 to 20 independent reverse-stress cases")

    report_cases: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, item in enumerate(cases, 1):
        case = _object(item, "reverse case")
        case_name = case.get("name")
        if (not isinstance(case_name, str) or not case_name
                or len(case_name) > 80 or case_name in names):
            raise ValueError("Case names must be unique, nonempty and <=80 chars")
        names.add(case_name)
        maintenance_multiple = _number(
            case.get("maintenance_multiplier"), "maintenance multiplier", 1, 5
        )
        days = case.get("horizon_days")
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 365:
            raise ValueError("horizon_days must be integer from 0 to 365")
        assumed_apr = _number(case.get("assumed_annual_apr"), "assumed APR", 0, 1)
        sensitivities = _object(
            case.get("relative_shock_multipliers"), "relative shock multipliers"
        )
        if set(sensitivities) != set(ids):
            raise ValueError("Every sleeve must have exactly one relative shock multiplier")
        sensitivity = [
            _number(sensitivities[id_], "relative shock multiplier", 0, 1)
            for id_ in ids
        ]
        loss_per_full_decline = sum(v * b for v, b in zip(values, sensitivity))
        new_interest = debt * assumed_apr * days / 365
        initial_nlv = nlv - new_interest
        initial_elv = elv - new_interest
        modeled_maint = maint * maintenance_multiple
        # v0.4.12 does not expose a top-level initial-margin claim. The
        # purchase-power boundary is omitted rather than invented.
        starting_excess = initial_elv - modeled_maint
        starting_buffer_difference = starting_excess - policy_floor * initial_nlv

        thresholds = {
            "POLICY_BUFFER": _boundary(
                starting_buffer_difference,
                loss_per_full_decline * (1 - policy_floor),
                maximum_drawdown,
            ),
            "MAINTENANCE_EXCESS_ZERO": _boundary(
                starting_excess, loss_per_full_decline, maximum_drawdown
            ),
            "NLV_ZERO": _boundary(
                initial_nlv, loss_per_full_decline, maximum_drawdown
            ),
        }
        # Static margin change and accrued simple interest are applied before
        # any market decline. The threshold 0 means already at/through boundary.
        reached = [(key, value) for key, value in thresholds.items() if value is not None]
        first_boundary = min((value for _, value in reached), default=None)
        first_codes = (
            sorted(key for key, value in reached if abs(value - first_boundary) < 1e-10)
            if first_boundary is not None else []
        )
        if first_boundary == 0:
            state = "ILLUSTRATIVE_ALREADY_AT_THRESHOLD"
        elif first_boundary is None:
            state = "ILLUSTRATIVE_NO_THRESHOLD_IN_SCAN_RANGE"
        else:
            state = "ILLUSTRATIVE_THRESHOLD_WITHIN_SCAN"
        annual_interest_ratio = debt * assumed_apr / nlv
        if annual_interest_ratio > initial_interest_limit + 1e-12:
            finance_alert = "ASSUMED_FINANCING_BUDGET_EXCEEDED"
        else:
            finance_alert = None
        projected_nlv_at_cap = initial_nlv - loss_per_full_decline * maximum_drawdown
        projected_excess_at_cap = starting_excess - loss_per_full_decline * maximum_drawdown
        report_cases.append({
            "index": index,
            "private_name": case_name,
            "state": state,
            "earliest_threshold_fraction": first_boundary,
            "first_threshold_codes": first_codes,
            "threshold_fractions": thresholds,
            "assumed_interest_budget_issue": finance_alert,
            "assumed_annual_apr": assumed_apr,
            "horizon_days": days,
            "maintenance_multiplier": maintenance_multiple,
            "loss_per_100pct_benchmark_decline_usd": loss_per_full_decline,
            "new_simple_interest_usd": new_interest,
            "nlv_at_max_drawdown_usd": projected_nlv_at_cap,
            "excess_liquidity_at_max_drawdown_usd": projected_excess_at_cap,
            "interest_to_start_nlv": annual_interest_ratio,
            "relative_shock_multipliers": dict(zip(ids, sensitivity)),
        })

    return {
        "method": "atlaspm_offline_reverse_margin_thresholds",
        "status": "HYPOTHETICAL_REVERSE_STRESS_ONLY",
        "account_id": account_id,
        "upstream_provenance": "user_controlled_unsigned_local_json",
        "baseline_nlv_usd_claim": nlv,
        "baseline_elv_usd_claim": elv,
        "baseline_maintenance_usd_claim": maint,
        "financing_principal_usd_claim_unverified": debt,
        "min_excess_to_nlv_ratio": policy_floor,
        "maximum_drawdown_fraction": maximum_drawdown,
        "cases": report_cases,
        "IBKR_financing_debt_separately_verified": False,
        "broker_treasury_collateral_verified": False,
        "IBKR_order_bound_whatif_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Reverse thresholds are static algebra applied to assumptions, NOT IBKR liquidation trigger prices.",
            "The v0.4.12 baseline is unsigned and may be stale or synthetic; no new brokerage data were acquired.",
            "Each market shock is an explicitly assumed decline profile; no actual price/beta calibration or dynamic position rebalancing.",
            "Initial margin, purchasing power and collateral cannot be derived from this prior report.",
            "Interest uses a hypothetical flat simple APR on unverified principal; no loan tiers, settlement, income, fees or compounding.",
            "An already breached or never breached threshold has no predictive accuracy without verified account data.",
            "This output is never a broker What-If, institutional risk approval, order or trading instruction.",
        ],
    }


def redacted_reverse_summary(report: Mapping[str, Any], output_path: str) -> dict[str, Any]:
    account = str(report["account_id"])
    return {
        "written": output_path,
        "method": report["method"],
        "status": report["status"],
        "account_hint": "****" + account[-4:],
        "cases": [
            {
                "index": case["index"],
                "state": case["state"],
                "first_threshold_codes": case["first_threshold_codes"],
                "assumed_financing_budget_issue": case["assumed_interest_budget_issue"],
            }
            for case in report["cases"]
        ],
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Dollar values, shock fractions and case names are only in local_data/.",
            "No actual IBKR liquidation threshold or margin privilege was verified.",
        ],
    }
