"""v0.4.14: offline evidence-chain consistency and hypothetical-risk audit.

Input files are unsigned, user-controlled local JSON; hashes describe *bytes of
canonicalized data at audit time*, not broker authentication or provenance.
No network access, orders, margin approval, or real trading gate integration.
"""

from __future__ import annotations

import hashlib
import json
from math import isfinite
from typing import Any, Mapping

from atlaspm.ibkr_margin_stress import FIELD_PAIR
from atlaspm.ibkr_multiasset_budget import _correlation_matrix, _one_day_var
from atlaspm.ibkr_reverse_stress import _boundary

STAGE_METHODS = {
    "monitor": "ibkr_gateway_portfolio_summary_margin_monitor",
    "financing": "ibkr_offline_financing_margin_cross_check",
    "stress": "atlaspm_offline_unverified_margin_stress",
    "multiasset": "atlaspm_offline_multiasset_correlation_budget",
    "reverse": "atlaspm_offline_reverse_margin_thresholds",
}
FLAG_NAMES = (
    "live_execution_enabled", "ready_for_institutional_risk_gate",
    "IBKR_financing_debt_separately_verified",
)
TOLERANCE = 0.02


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be nonempty list")
    return value


def _number(value: Any, label: str, *, low: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be finite numeric data")
    n = float(value)
    if low is not None and n < low:
        raise ValueError(f"{label} below allowed range")
    return n


def _finite_json(value: Any) -> str:
    # Reject NaN/Infinity anywhere in source instead of silently hashing invalid JSON.
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Evidence report contains invalid JSON data") from None


def _same(a: Any, b: Any, reason: str, issues: list[str]) -> None:
    if abs(_number(a, reason) - _number(b, reason)) > TOLERANCE:
        issues.append(reason)


def _finite_case_indices(cases: list[Any], label: str) -> list[dict[str, Any]]:
    rows = [_object(row, label) for row in cases]
    if len(rows) > 20:
        raise ValueError("Too many hypothetical scenarios in source report")
    for expected, row in enumerate(rows, start=1):
        ix = row.get("index")
        if isinstance(ix, bool) or not isinstance(ix, int) or ix != expected:
            raise ValueError("Report scenario index sequence is invalid")
    return rows


def audit_ibkr_risk_chain(
    reports: Mapping[str, Any], *, baseline_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Check five persisted offline reports; never elevate any trust flag.

    Fail-close in the result for contradictory identities, statuses or arithmetic.
    Gross scenario alerts are always hypothetical and never imply real orders.
    """
    supplied = _object(reports, "report mapping")
    if set(supplied) != set(STAGE_METHODS):
        raise ValueError("Five named stage reports are required")
    stages = {name: _object(supplied[name], name) for name in STAGE_METHODS}
    # Early parse of every stage disallows truncated files and NaN/Infinity.
    hashes = {
        name: hashlib.sha256(_finite_json(stages[name]).encode("ascii")).hexdigest()
        for name in STAGE_METHODS
    }
    issues: list[str] = []
    for name, stage in stages.items():
        if stage.get("method") != STAGE_METHODS[name]:
            issues.append("STAGE_METHOD_MISMATCH_" + name.upper())
        for flag in FLAG_NAMES:
            if stage.get(flag) is not False:
                issues.append("UNSUPPORTED_PROVENANCE_OR_AUTHORITY_" + name.upper())
        if stage.get("broker_treasury_collateral_verified") is not False:
            issues.append("UNSUPPORTED_COLLATERAL_VERIFICATION_" + name.upper())
    for name in ("stress", "multiasset", "reverse"):
        if stages[name].get("IBKR_order_bound_whatif_verified") is not False:
            issues.append("UNSUPPORTED_BROKER_WHATIF_" + name.upper())

    account = stages["monitor"].get("account_id")
    if not isinstance(account, str) or not account or len(account) > 64:
        raise ValueError("Monitor account identity must be nonempty")
    for name, report in stages.items():
        if report.get("account_id") != account:
            issues.append("ACCOUNT_ID_MISMATCH_" + name.upper())

    monitor, financing, stress, multi, reverse = (
        stages[x] for x in ("monitor", "financing", "stress", "multiasset", "reverse")
    )
    if monitor.get("monitor_state") not in {"NORMAL", "WATCH", "CRITICAL"}:
        issues.append("MONITOR_NOT_DATA_CLEAN")
    if monitor.get("data_integrity_block_reasons") != []:
        issues.append("MONITOR_INTEGRITY_BLOCKED")
    if financing.get("status") != "FIELDS_CONSISTENT_REVIEW_REQUIRED":
        issues.append("FINANCING_CLAIMS_NOT_RECONCILED")
    if financing.get("check_issues") != []:
        issues.append("FINANCING_CLAIM_ISSUES_PRESENT")
    if stress.get("status") != "HYPOTHETICAL_STRESS_ONLY":
        issues.append("STRESS_STATUS_MISMATCH")
    if multi.get("status") != "HYPOTHETICAL_RISK_BUDGET_ONLY":
        issues.append("MULTIASSET_STATUS_MISMATCH")
    if reverse.get("status") != "HYPOTHETICAL_REVERSE_STRESS_ONLY":
        issues.append("REVERSE_STATUS_MISMATCH")
    if stress.get("upstream_monitor_state_claim") != monitor.get("monitor_state"):
        issues.append("MONITOR_STATE_DRIFT")
    source = monitor.get("source")
    if source not in {"synthetic_ibkr_fixture", "local_authenticated_ibkr_gateway"}:
        issues.append("MONITOR_SOURCE_UNSUPPORTED")
    stmt_source = financing.get("statement_source_claim")
    if (source == "synthetic_ibkr_fixture" and stmt_source != "synthetic_margin_fixture"
            or source == "local_authenticated_ibkr_gateway"
            and stmt_source != "manually_transcribed_broker_summary"):
        issues.append("SYNTHETIC_OR_MANUAL_SOURCE_MIXED")

    mon_vals = _object(monitor.get("margin_summary_usd_claim"), "monitor margin")
    fin_vals = _object(financing.get("margin_summary_claim_usd"), "financing margin")
    stress_vals = _object(stress.get("baseline_margin_usd_claim"), "stress margin")
    for internal, external in FIELD_PAIR.items():
        x = _number(mon_vals.get(internal), "baseline monitor")
        y = _number(fin_vals.get(external), "baseline financing")
        z = _number(stress_vals.get(internal), "baseline stress")
        _same(x, y, "MONITOR_FINANCING_MARGIN_DRIFT_" + internal.upper(), issues)
        _same(x, z, "MONITOR_STRESS_MARGIN_DRIFT_" + internal.upper(), issues)
    nlv = _number(mon_vals["net_liquidation"], "baseline NLV", low=0.01)
    elv = _number(mon_vals["equity_with_loan_value"], "baseline ELV", low=0)
    maint = _number(mon_vals["maintenance_margin"], "baseline maintenance", low=0)
    init = _number(mon_vals["initial_margin"], "baseline initial", low=0)
    if abs(elv - init - _number(mon_vals["available_funds"], "available funds")) > 1:
        issues.append("BASELINE_AVAILABLE_FUNDS_FORMULA_BAD")
    if abs(elv - maint - _number(mon_vals["excess_liquidity"], "excess liquidity")) > 1:
        issues.append("BASELINE_EXCESS_FORMULA_BAD")

    loan = _number(
        _object(financing.get("financing_claim_usd"), "finance amounts")
        .get("loan_principal_from_statement"), "loan claim", low=0,
    )
    _same(
        loan, _object(financing["financing_claim_usd"], "finance amounts")
        .get("loan_principal_from_activity"), "LOAN_ACTIVITY_CLAIM_DRIFT", issues,
    )
    _same(loan, stress.get("financing_principal_usd_claim"),
          "FINANCING_STRESS_LOAN_DRIFT", issues)
    _same(loan, multi.get("financing_principal_usd_claim"),
          "FINANCING_MULTI_LOAN_DRIFT", issues)
    _same(loan, reverse.get("financing_principal_usd_claim_unverified"),
          "FINANCING_REVERSE_LOAN_DRIFT", issues)

    _same(nlv, multi.get("starting_nlv_usd_claim"),
          "STRESS_MULTI_NLV_DRIFT", issues)
    _same(elv, multi.get("starting_equity_with_loan_value_usd_claim"),
          "STRESS_MULTI_ELV_DRIFT", issues)
    _same(maint, multi.get("starting_maintenance_usd_claim"),
          "STRESS_MULTI_MAINTENANCE_DRIFT", issues)
    _same(nlv, reverse.get("baseline_nlv_usd_claim"),
          "MULTI_REVERSE_NLV_DRIFT", issues)
    _same(elv, reverse.get("baseline_elv_usd_claim"),
          "MULTI_REVERSE_ELV_DRIFT", issues)
    _same(maint, reverse.get("baseline_maintenance_usd_claim"),
          "MULTI_REVERSE_MAINTENANCE_DRIFT", issues)

    assumed_exposure = _number(
        stress.get("assumed_shockable_position_value_usd"), "stress exposure", low=0
    )
    _same(assumed_exposure, multi.get("total_shockable_value_usd_assumption"),
          "STRESS_MULTI_EXPOSURE_DRIFT", issues)
    rows = _finite_case_indices(_list(stress.get("scenarios"), "stress cases"), "stress case")
    stress_alert_count = 0
    stress_apr = _number(stress.get("assumed_annual_financing_apr"), "stress APR", low=0)
    for r in rows:
        loss = _number(r.get("hypothetical_market_loss_usd"), "stress loss")
        decline = _number(r.get("assumed_shock_fraction"), "stress shock", low=0)
        if decline > 1:
            issues.append("STRESS_SHOCK_OUT_OF_RANGE")
        _same(loss, assumed_exposure * decline, "STRESS_SHOCK_FORMULA_DRIFT", issues)
        cost = _number(r.get("new_simple_financing_interest_usd"), "stress interest", low=0)
        days = r.get("horizon_days")
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 365:
            raise ValueError("Stress horizon invalid")
        _same(cost, loan * stress_apr * days / 365,
              "STRESS_INTEREST_FORMULA_DRIFT", issues)
        multiplier = _number(r.get("assumed_maintenance_multiplier"),
                             "stress maintenance multiplier", low=1)
        _same(maint * multiplier, r.get("hypothetical_maintenance_margin_usd"),
              "STRESS_MAINTENANCE_MULTIPLIER_DRIFT", issues)
        _same(init * multiplier, r.get("hypothetical_initial_margin_usd"),
              "STRESS_INITIAL_MULTIPLIER_DRIFT", issues)
        _same(nlv - loss - cost, r.get("hypothetical_nlv_usd"),
              "STRESS_NLV_FORMULA_DRIFT", issues)
        _same(elv - loss - cost, r.get("hypothetical_equity_with_loan_value_usd"),
              "STRESS_ELV_FORMULA_DRIFT", issues)
        mr = _number(r.get("hypothetical_maintenance_margin_usd"), "stress maintenance", low=0)
        ir = _number(r.get("hypothetical_initial_margin_usd"), "stress initial", low=0)
        _same(elv - loss - cost - mr, r.get("hypothetical_excess_liquidity_usd"),
              "STRESS_EXCESS_FORMULA_DRIFT", issues)
        _same(elv - loss - cost - ir, r.get("hypothetical_available_funds_usd"),
              "STRESS_AVAILABLE_FORMULA_DRIFT", issues)
        if r.get("scenario_state") not in (
            "ILLUSTRATIVE_ABOVE_FLOOR", "ILLUSTRATIVE_WATCH",
            "ILLUSTRATIVE_DEFICIT"
        ):
            issues.append("STRESS_CASE_STATE_UNSUPPORTED")
        elif r["scenario_state"] != "ILLUSTRATIVE_ABOVE_FLOOR":
            stress_alert_count += 1

    sleeves = _list(multi.get("asset_sleeves"), "risk sleeves")
    if len(sleeves) > 12:
        raise ValueError("Too many asset sleeves")
    ids: list[str] = []
    values: list[float] = []
    vols: list[float] = []
    for item in sleeves:
        o = _object(item, "risk sleeve")
        identifier = o.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError("Malformed or repeated risk sleeve")
        ids.append(identifier)
        values.append(_number(o.get("value_usd_assumption"), "sleeve value", low=0))
        vols.append(_number(o.get("annual_volatility_assumption"), "sleeve vol", low=0))
    _same(assumed_exposure, sum(values), "RISK_SLEEVES_TOTAL_DRIFT", issues)
    if multi.get("asset_sleeve_count") != len(ids):
        issues.append("RISK_SLEEVE_COUNT_DRIFT")
    scenarios = _finite_case_indices(
        _list(multi.get("scenarios"), "multiasset scenarios"), "multiasset scenario"
    )
    multi_alert_count = 0
    for case in scenarios:
        marks = _object(case.get("asset_returns"), "multiasset return map")
        if set(marks) != set(ids):
            issues.append("MULTI_ASSET_RETURN_MAP_DRIFT")
            continue
        pnl = sum(
            v * _number(marks[k], "asset return")
            for k, v in zip(ids, values)
        )
        _same(pnl, case.get("assumed_signed_market_pnl_usd"),
              "MULTI_ASSET_PNL_DRIFT", issues)
        interest = _number(case.get("new_simple_financing_interest_usd"),
                           "multiasset interest", low=0)
        assumed_apr = _number(case.get("assumed_apr"), "multiasset APR", low=0)
        days = case.get("horizon_days")
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 365:
            raise ValueError("Multiasset horizon invalid")
        _same(loan * assumed_apr * days / 365, interest,
              "MULTI_ASSET_INTEREST_DRIFT", issues)
        _same(nlv + pnl - interest, case.get("hypothetical_nlv_usd"),
              "MULTI_ASSET_NLV_DRIFT", issues)
        _same(elv + pnl - interest, case.get("hypothetical_equity_with_loan_value_usd"),
              "MULTI_ASSET_ELV_DRIFT", issues)
        m = _number(case.get("hypothetical_maintenance_requirement_usd"),
                    "multiasset maintenance", low=0)
        _same(elv + pnl - interest - m,
              case.get("hypothetical_excess_liquidity_usd"),
              "MULTI_ASSET_EXCESS_DRIFT", issues)
        corr = _correlation_matrix(
            case.get("correlation_matrix_assumption"), len(ids), "stored correlation"
        )
        var = _one_day_var(
            values, vols, corr,
            _number(case.get("volatility_multiplier"), "vol multiplier", low=0),
        )
        _same(var, case.get("one_day_99pct_delta_normal_var_usd"),
              "MULTI_ASSET_VAR_DRIFT", issues)
        if case.get("state") not in (
            "HYPOTHETICAL_WITHIN_LIMITS", "HYPOTHETICAL_LIMIT_BREACH",
            "HYPOTHETICAL_DEFICIT"
        ):
            issues.append("MULTI_CASE_STATE_UNSUPPORTED")
        elif case["state"] != "HYPOTHETICAL_WITHIN_LIMITS":
            multi_alert_count += 1

    r_cases = _finite_case_indices(_list(reverse.get("cases"), "reverse cases"),
                                   "reverse case")
    floor = _number(reverse.get("min_excess_to_nlv_ratio"), "reverse floor", low=0)
    cap = _number(reverse.get("maximum_drawdown_fraction"), "reverse cap", low=0)
    if floor >= 1 or cap <= 0 or cap > 1:
        raise ValueError("Stored reverse stress bounds invalid")
    reverse_alert_count = 0
    for row in r_cases:
        multipliers = _object(row.get("relative_shock_multipliers"), "reverse sensitivity")
        if set(multipliers) != set(ids):
            issues.append("REVERSE_ASSET_SENSITIVITY_DRIFT")
            continue
        loss_rate = sum(
            values[i] * _number(multipliers[k], "reverse multiplier", low=0)
            for i, k in enumerate(ids)
        )
        _same(loss_rate, row.get("loss_per_100pct_benchmark_decline_usd"),
              "REVERSE_LOSS_SENSITIVITY_DRIFT", issues)
        apr = _number(row.get("assumed_annual_apr"), "reverse APR", low=0)
        days = row.get("horizon_days")
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 365:
            raise ValueError("Reverse horizon invalid")
        cost = loan * apr * days / 365
        _same(cost, row.get("new_simple_interest_usd"),
              "REVERSE_INTEREST_DRIFT", issues)
        maint_mult = _number(row.get("maintenance_multiplier"), "maintenance multiple", low=1)
        starting_nlv = nlv - cost
        starting_excess = elv - cost - maint * maint_mult
        _same(starting_nlv - loss_rate * cap, row.get("nlv_at_max_drawdown_usd"),
              "REVERSE_NLV_AT_CAP_DRIFT", issues)
        _same(starting_excess - loss_rate * cap,
              row.get("excess_liquidity_at_max_drawdown_usd"),
              "REVERSE_EXCESS_AT_CAP_DRIFT", issues)
        expected_thresholds = {
            "POLICY_BUFFER": _boundary(
                starting_excess - floor * starting_nlv,
                loss_rate * (1 - floor), cap,
            ),
            "MAINTENANCE_EXCESS_ZERO": _boundary(starting_excess, loss_rate, cap),
            "NLV_ZERO": _boundary(starting_nlv, loss_rate, cap),
        }
        stored = _object(row.get("threshold_fractions"), "reverse thresholds")
        if set(expected_thresholds) != set(stored):
            issues.append("REVERSE_THRESHOLD_KEYS_DRIFT")
        else:
            for key, expected in expected_thresholds.items():
                actual = stored[key]
                if (expected is None) != (actual is None):
                    issues.append("REVERSE_THRESHOLD_DRIFT")
                elif expected is not None:
                    _same(expected, actual, "REVERSE_THRESHOLD_DRIFT", issues)
        finite = [v for v in expected_thresholds.values() if v is not None]
        first = min(finite) if finite else None
        first_codes = sorted(
            name for name, val in expected_thresholds.items()
            if first is not None and val is not None and abs(val - first) < 1e-10
        )
        if row.get("first_threshold_codes") != first_codes:
            issues.append("REVERSE_FIRST_THRESHOLD_CODES_DRIFT")
        stored_first = row.get("earliest_threshold_fraction")
        if (first is None) != (stored_first is None):
            issues.append("REVERSE_FIRST_THRESHOLD_DRIFT")
        elif first is not None:
            _same(first, stored_first, "REVERSE_FIRST_THRESHOLD_DRIFT", issues)
        if row.get("state") not in (
            "ILLUSTRATIVE_NO_THRESHOLD_IN_SCAN_RANGE",
            "ILLUSTRATIVE_THRESHOLD_WITHIN_SCAN",
            "ILLUSTRATIVE_ALREADY_AT_THRESHOLD",
        ):
            issues.append("REVERSE_CASE_STATE_UNSUPPORTED")
        elif row["state"] != "ILLUSTRATIVE_NO_THRESHOLD_IN_SCAN_RANGE":
            reverse_alert_count += 1

    if baseline_audit is not None:
        old = _object(baseline_audit, "baseline audit")
        if (old.get("method") != "atlaspm_local_ibkr_risk_chain_audit"
                or old.get("account_id") != account):
            issues.append("BASELINE_AUDIT_IDENTITY_MISMATCH")
        old_hashes = old.get("source_sha256")
        if not isinstance(old_hashes, dict) or set(old_hashes) != set(STAGE_METHODS):
            issues.append("BASELINE_AUDIT_DIGESTS_MISSING")
        else:
            for stage in STAGE_METHODS:
                if old_hashes.get(stage) != hashes[stage]:
                    issues.append("SOURCE_CHANGED_SINCE_BASELINE_" + stage.upper())

    unique_issues = sorted(set(issues))
    hypothetical_risk = (
        stress_alert_count + multi_alert_count + reverse_alert_count > 0
        or monitor.get("monitor_state") in {"WATCH", "CRITICAL"}
    )
    state = (
        "CHAIN_INCONSISTENT" if unique_issues else
        "HYPOTHETICAL_ALERTS_PRESENT" if hypothetical_risk else
        "HYPOTHETICAL_CLEAR_NOT_VERIFIED"
    )
    return {
        "method": "atlaspm_local_ibkr_risk_chain_audit",
        "status": state,
        "account_id": account,
        "integrity_issues": unique_issues,
        "upstream_monitor_state": monitor.get("monitor_state"),
        "hypothetical_stress_alert_count": stress_alert_count,
        "hypothetical_multiasset_alert_count": multi_alert_count,
        "hypothetical_reverse_threshold_count": reverse_alert_count,
        "source_sha256": hashes,
        "source_digest_method": "sha256_canonical_sorted_json_unsigned",
        "baseline_comparison_performed": baseline_audit is not None,
        "broker_source_independently_authenticated": False,
        "IBKR_financing_debt_separately_verified": False,
        "broker_treasury_collateral_verified": False,
        "IBKR_order_bound_whatif_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "All five input reports are unsigned, user-controlled JSON. Their SHA-256 digests detect changes between local audits but DO NOT authenticate IBKR origin.",
            "Arithmetic consistency does not verify broker margin, loan principal, collateral, account permissions or operational funding.",
            "Alerts reflect hypothetical scenario assumptions, not actual broker margin calls, liquidation timing or trading permission.",
            "No result from this audit permits order routing, risk gate approval or margin borrowing.",
        ],
    }


def redacted_audit_summary(report: Mapping[str, Any], output: str) -> dict[str, Any]:
    return {
        "written": output,
        "method": report["method"],
        "status": report["status"],
        "account_hint": "****" + str(report["account_id"])[-4:],
        "integrity_issues": report["integrity_issues"],
        "hypothetical_stress_alert_count": report["hypothetical_stress_alert_count"],
        "hypothetical_multiasset_alert_count": report["hypothetical_multiasset_alert_count"],
        "hypothetical_reverse_threshold_count": report["hypothetical_reverse_threshold_count"],
        "baseline_comparison_performed": report["baseline_comparison_performed"],
        "broker_source_independently_authenticated": False,
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Only masked account suffix, counts and fixed diagnostics appear in stdout.",
            "A clean local file hash never establishes genuine broker provenance or trading approval.",
        ],
    }
