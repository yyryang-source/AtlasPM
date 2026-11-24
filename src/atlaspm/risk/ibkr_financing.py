"""Offline IBKR financing and margin cross-check, with permanent risk-gate veto.

This module accepts separately SUPPLIED account-summary and financing numbers.
Neither a matching user file nor a synthetic fixture proves broker provenance,
a margin loan, collateral eligibility, or permission to route orders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

from atlaspm.execution.ibkr_mapping import diagnose_ibkr_contract_mapping
from atlaspm.execution.institutional_ledger import _timestamp


SUMMARY_FIELDS = (
    "NetLiquidation", "EquityWithLoanValue", "InitMarginReq",
    "MaintMarginReq", "AvailableFunds", "ExcessLiquidity",
)
SOURCES = {"synthetic_margin_fixture", "manually_transcribed_broker_summary"}
TOLERANCE_USD = 1.0


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _money(value: Any, field: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value):
        raise ValueError(f"{field} must be a finite JSON number")
    result = float(value)
    if nonnegative and result < 0:
        raise ValueError(f"{field} cannot be negative")
    return result


def _utc(value: Any, field: str) -> datetime:
    # The existing AtlasPM timestamp validator rejects missing/naive times.
    parsed = _timestamp(value, field)
    return parsed.astimezone(timezone.utc)


def reconcile_ibkr_financing_margin(
    capture_data: Mapping[str, Any],
    mapping_data: Mapping[str, Any],
    statement_data: Mapping[str, Any],
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Cross-check a fresh IBKR mapping with SUPPLIED margin and debt evidence.

    Always returns ready_for_institutional_risk_gate=False. Never infers
    actual financing principal from signed settledcash or the USD.interest
    (interest-rate) field.
    """
    capture = _object(capture_data, "capture")
    statement = _object(statement_data, "margin statement")
    source = statement.get("source")
    if source not in SOURCES:
        raise ValueError("Unsupported margin statement source")
    if not isinstance(statement.get("account_id"), str) or not statement["account_id"]:
        raise ValueError("Statement account_id is required")
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    mapping = diagnose_ibkr_contract_mapping(capture, mapping_data, now_utc=now)
    account_id = mapping["account_id"]
    issues: list[str] = []
    warnings: list[str] = []
    if mapping["mapping_status"] != "IDENTITIES_MAPPED_REVIEW_REQUIRED":
        issues.append("IBKR_CONTRACT_MAPPING_BLOCKED")
    if statement["account_id"] != account_id:
        issues.append("MARGIN_ACCOUNT_ID_MISMATCH")
    if statement.get("currency") != "USD" or statement.get("segment") != "SECURITIES":
        issues.append("MARGIN_CURRENCY_OR_SEGMENT_UNSUPPORTED")
    if statement.get("basis") != "CURRENT":
        issues.append("MARGIN_BASIS_NOT_CURRENT")

    captured = _utc(capture.get("captured_at_utc"), "captured_at_utc")
    recorded = statement.get("as_of_utc")
    if recorded == "SAME_AS_SYNTHETIC_CAPTURE":
        if source != "synthetic_margin_fixture" or capture.get("source") != "synthetic_ibkr_fixture":
            raise ValueError("Synthetic timestamp substitution requires synthetic sources")
        as_of = captured
    else:
        as_of = _utc(recorded, "as_of_utc")
    age = (now - as_of).total_seconds()
    max_age = _money(statement.get("max_age_seconds"), "max_age_seconds", nonnegative=True)
    if not 0 < max_age <= 3600:
        raise ValueError("max_age_seconds must be in (0,3600]")
    if age < -1:
        issues.append("MARGIN_SNAPSHOT_FUTURE_DATED")
    if age > max_age:
        issues.append("MARGIN_SNAPSHOT_STALE")
    skew = abs((as_of - captured).total_seconds())
    max_skew = _money(statement.get("max_capture_skew_seconds"),
                      "max_capture_skew_seconds", nonnegative=True)
    if max_skew > 600:
        raise ValueError("max_capture_skew_seconds must be <= 600")
    if skew > max_skew:
        issues.append("MARGIN_CAPTURE_TIMESTAMP_SKEW")
    if source == "synthetic_margin_fixture" and capture.get("source") != "synthetic_ibkr_fixture":
        issues.append("SYNTHETIC_MARGIN_WITH_NON_SYNTHETIC_CAPTURE")

    summary_raw = _object(statement.get("margin_summary"), "margin_summary")
    margin = {key: _money(summary_raw.get(key), key) for key in SUMMARY_FIELDS}
    for key in ("NetLiquidation", "EquityWithLoanValue", "InitMarginReq", "MaintMarginReq"):
        if margin[key] < 0:
            issues.append(f"NEGATIVE_{key.upper()}")
    gateway_nlv = mapping["IBKR_netliquidationvalue_field_usd"]
    if abs(margin["NetLiquidation"] - gateway_nlv) > TOLERANCE_USD:
        issues.append("MARGIN_NLV_DIFFERS_FROM_GATEWAY")
    expected_available = margin["EquityWithLoanValue"] - margin["InitMarginReq"]
    expected_excess = margin["EquityWithLoanValue"] - margin["MaintMarginReq"]
    if abs(margin["AvailableFunds"] - expected_available) > TOLERANCE_USD:
        issues.append("AVAILABLE_FUNDS_FORMULA_MISMATCH")
    if abs(margin["ExcessLiquidity"] - expected_excess) > TOLERANCE_USD:
        issues.append("EXCESS_LIQUIDITY_FORMULA_MISMATCH")
    if margin["AvailableFunds"] < 0:
        issues.append("NEGATIVE_AVAILABLE_FUNDS")
    if margin["ExcessLiquidity"] <= 0:
        issues.append("NONPOSITIVE_EXCESS_LIQUIDITY")

    financing_raw = _object(statement.get("financing"), "financing")
    loan = _money(financing_raw.get("loan_principal_from_statement_usd"),
                  "loan_principal_from_statement_usd", nonnegative=True)
    activity = _money(financing_raw.get("loan_principal_from_activity_usd"),
                      "loan_principal_from_activity_usd", nonnegative=True)
    accrued = _money(financing_raw.get("accrued_interest_from_statement_usd"),
                     "accrued_interest_from_statement_usd", nonnegative=True)
    if abs(loan - activity) > 0.01:
        issues.append("FINANCING_PRINCIPAL_CLAIMS_MISMATCH")

    raw = _object(capture.get("raw"), "capture.raw")
    account = _object(raw.get("selected_account"), "selected_account")
    if account.get("tradingType") == "CASH" or account.get("type") == "CASH":
        if loan > 0 or activity > 0:
            issues.append("CASH_ACCOUNT_WITH_FINANCING_CLAIM")

    minimum_buffer = _money(statement.get("min_excess_liquidity_to_nlv"),
                            "min_excess_liquidity_to_nlv", nonnegative=True)
    if minimum_buffer > 1:
        raise ValueError("min_excess_liquidity_to_nlv must be <= 1")
    buffer_ratio = (
        margin["ExcessLiquidity"] / margin["NetLiquidation"]
        if margin["NetLiquidation"] > 0 else None
    )
    if buffer_ratio is None or buffer_ratio < minimum_buffer:
        issues.append("MARGIN_BUFFER_BELOW_POLICY_FLOOR")

    warnings.extend([
        "Both financing amounts are supplied claims, not independently verified broker debt.",
        "No authenticated account-summary or financing/statement adapter is implemented here.",
        "Signed IBKR settledcash does not establish loan principal; USD.interest denotes a rate, not accrued interest.",
        "NLV is checked against the Gateway NLV, not reconstructed by subtracting debt again.",
        "Account-level available funds and excess liquidity are not an order-bound What-If preview.",
        "No broker-recognized Treasury collateral or securities-specific haircut is verified.",
        "Keep the complete report and input statement under ignored local_data/ only.",
    ])
    blocked = bool(issues)
    return {
        "method": "ibkr_offline_financing_margin_cross_check",
        "account_id": account_id,
        "statement_source_claim": source,
        "mapping_status": mapping["mapping_status"],
        "mapping_block_reasons": mapping["mapping_block_reasons"],
        "status": "BLOCK_FINANCING_MARGIN" if blocked else "FIELDS_CONSISTENT_REVIEW_REQUIRED",
        "check_issues": sorted(set(issues)),
        "margin_snapshot_age_seconds": age,
        "margin_capture_skew_seconds": skew,
        "margin_summary_claim_usd": margin,
        "financing_claim_usd": {
            "loan_principal_from_statement": loan,
            "loan_principal_from_activity": activity,
            "accrued_interest_from_statement": accrued,
        },
        "comparisons_usd": {
            "nlv_minus_gateway_nlv": margin["NetLiquidation"] - gateway_nlv,
            "available_funds_minus_elv_less_initial": margin["AvailableFunds"] - expected_available,
            "excess_liquidity_minus_elv_less_maintenance": margin["ExcessLiquidity"] - expected_excess,
            "loan_principal_statement_minus_activity": loan - activity,
        },
        "excess_liquidity_to_nlv_claim": buffer_ratio,
        "min_excess_liquidity_to_nlv": minimum_buffer,
        "IBKR_financing_debt_separately_verified": False,
        "IBKR_margin_requirements_independently_verified": False,
        "broker_treasury_collateral_verified": False,
        "IBKR_margin_or_whatif_verified": False,
        "source_authentication_independently_proven": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": warnings,
    }


def financing_redacted_summary(report: Mapping[str, Any], output: str) -> dict[str, Any]:
    """Never display real account IDs, loan balances, NLV, or marks on stdout."""
    account = str(report["account_id"])
    return {
        "written": output,
        "method": report["method"],
        "account_hint": "****" + account[-4:],
        "status": report["status"],
        "mapping_status": report["mapping_status"],
        "check_issues": report["check_issues"],
        "IBKR_financing_debt_separately_verified": False,
        "IBKR_margin_requirements_independently_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Cross-check only; account and financing figures remain private.",
            "Consistent fields do not prove IBKR loan, collateral, margin approval, or trade permission.",
        ],
    }
