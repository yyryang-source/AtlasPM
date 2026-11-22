"""Offline external-broker-export vs local portfolio-ledger reconciliation.

This module does NOT authenticate a snapshot, fetch brokerage accounts, place
orders or grant margin buying power. It is an independent read-only gate that
fails closed on mismatched accounts, positions, values, age, and liabilities.
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

from atlaspm.institutional_ledger import build_institutional_ledger, _timestamp


def _obj(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value.strip()


def _num(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _nonnegative(value: Any, name: str) -> float:
    number = _num(value, name)
    if number < 0:
        raise ValueError(f"{name} must be nonnegative")
    return number


def _compare(
    label: str, local: float, external: float, tolerance: float,
    discrepancies: list[dict[str, Any]],
) -> None:
    delta = local - external
    if abs(delta) > tolerance + 1e-9:
        discrepancies.append({
            "field": label,
            "local": local,
            "imported_broker": external,
            "difference_local_minus_import": delta,
            "allowed_difference": tolerance,
        })


def reconcile_imported_broker_snapshot(
    ledger_payload: Mapping[str, Any], snapshot_payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Independent broker-export contract validation; does not authorize trades.

    Input source label is a claim from local JSON, NOT proof of brokerage origin.
    """
    ledger = build_institutional_ledger(ledger_payload)
    snap = _obj(snapshot_payload, "snapshot")
    if snap.get("source") != "imported_broker_export":
        raise ValueError("source must be imported_broker_export")
    account_id = _str(snap.get("account_id"), "account_id")
    accounts = [a for a in ledger["accounts"] if a["account_id"] == account_id]
    if len(accounts) != 1:
        raise ValueError("Broker export account_id must select exactly one ledger account")
    account = accounts[0]
    if snap.get("account_type") != account["account_type"]:
        raise ValueError("Broker export account_type does not match the ledger")
    if snap.get("currency") != "USD":
        raise ValueError("Only USD broker snapshots are supported")

    current = _timestamp(ledger["valuation_at"], "valuation_at")
    captured = _timestamp(snap.get("captured_at"), "captured_at")
    age = (current - captured).total_seconds()
    if age < -1:
        raise ValueError("Broker export captured_at is later than ledger valuation")
    max_age = _nonnegative(snap.get("max_snapshot_age_seconds"), "max_snapshot_age_seconds")
    if max_age <= 0:
        raise ValueError("max_snapshot_age_seconds must be positive")
    cash_tol = _nonnegative(snap.get("cash_tolerance_usd", 0.01), "cash_tolerance_usd")
    value_tol = _nonnegative(
        snap.get("position_value_tolerance_usd", 0.01),
        "position_value_tolerance_usd",
    )
    quantity_tol = _nonnegative(
        snap.get("equity_quantity_tolerance", 1e-6), "equity_quantity_tolerance"
    )
    if quantity_tol > 1:
        raise ValueError("equity_quantity_tolerance must be at most one share")
    positions = snap.get("positions")
    if not isinstance(positions, list):
        raise ValueError("Broker export positions must be an array")

    discrepancies: list[dict[str, Any]] = []
    blockers: list[str] = []
    if age > max_age:
        blockers.append("IMPORTED_BROKER_EXPORT_STALE")
    if not account["account_ledger_ready_for_risk_review"]:
        blockers.append("LOCAL_LEDGER_NOT_READY_FOR_RECONCILIATION")

    fields = (
        "settled_cash", "unsettled_receivable", "unsettled_payable",
        "margin_debt", "accrued_financing_interest",
    )
    imported_fields: dict[str, float] = {}
    for field in fields:
        external = _num(snap.get(field), field)
        imported_fields[field] = external
        _compare(field, account[field], external, cash_tol, discrepancies)
    broker_nlv = _num(snap.get("net_liquidation"), "net_liquidation")
    _compare("net_liquidation", account["net_liquidation_calculated"], broker_nlv,
             cash_tol, discrepancies)
    available_funds = _num(snap.get("available_funds"), "available_funds")
    excess_liquidity = _num(snap.get("excess_liquidity"), "excess_liquidity")
    if available_funds < 0 or excess_liquidity < 0:
        blockers.append("IMPORTED_BROKER_LIQUIDITY_NEGATIVE")
    if account["account_type"] == "cash" and imported_fields["margin_debt"] > 0:
        blockers.append("IMPORTED_CASH_ACCOUNT_WITH_MARGIN_DEBT")

    # Quantity-keyed comparison: an equity ticker is never silently matched
    # to a Treasury CUSIP or OCC option contract. No fuzzy symbol aliases.
    quantity_keys = {"equity": "shares", "treasury": "face_value", "option": "contracts"}
    local_by_key = {
        (p["type"], p["identifier"]): p for p in account["positions"]
    }
    broker_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    imported_positions_value = 0.0
    for raw in positions:
        p = _obj(raw, "broker.position")
        kind = p.get("type")
        if kind not in quantity_keys:
            raise ValueError("Unsupported broker position type")
        identifier = _str(p.get("identifier"), "broker.position.identifier")
        key = (kind, identifier)
        if key in broker_by_key:
            raise ValueError("Duplicate position in broker export")
        quantity = _num(p.get(quantity_keys[kind]), f"{identifier}.{quantity_keys[kind]}")
        if kind != "option" and quantity <= 0:
            raise ValueError("Broker long equity/Treasury quantity must be positive")
        if kind == "option" and (quantity == 0 or int(quantity) != quantity):
            raise ValueError("Broker option contracts must be signed nonzero integers")
        multiplier = None
        if kind == "option":
            multiplier = _num(p.get("multiplier"), "option.multiplier")
            if multiplier <= 0 or int(multiplier) != multiplier:
                raise ValueError("Option multiplier must be a positive integer")
        value = _num(p.get("market_value"), "broker.position.market_value")
        imported_positions_value += value
        broker_by_key[key] = {
            "type": kind, "identifier": identifier,
            quantity_keys[kind]: quantity, "market_value": value,
            **({"multiplier": multiplier} if kind == "option" else {}),
        }

    missing = sorted(f"{kind}:{security}" for kind, security in local_by_key.keys() - broker_by_key.keys())
    unexpected = sorted(f"{kind}:{security}" for kind, security in broker_by_key.keys() - local_by_key.keys())
    if missing:
        blockers.append("BROKER_EXPORT_MISSING_LOCAL_POSITIONS")
    if unexpected:
        blockers.append("BROKER_EXPORT_HAS_UNKNOWN_POSITIONS")
    for key in sorted(local_by_key.keys() & broker_by_key.keys()):
        kind, identifier = key
        local = local_by_key[key]
        external = broker_by_key[key]
        attr = quantity_keys[kind]
        qtol = quantity_tol if kind == "equity" else (0.01 if kind == "treasury" else 0)
        _compare(f"{kind}:{identifier}:{attr}", float(local[attr]), external[attr],
                 qtol, discrepancies)
        _compare(f"{kind}:{identifier}:market_value", local["market_value"],
                 external["market_value"], value_tol, discrepancies)
        if kind == "option":
            _compare(f"{kind}:{identifier}:multiplier", local["multiplier"],
                     external["multiplier"], 0, discrepancies)

    # Independently reconcile the imported broker's own claimed NLV from
    # imported cash/debt/position values before trusting comparison alone.
    imported_arithmetic_nlv = (
        imported_fields["settled_cash"]
        + imported_fields["unsettled_receivable"]
        + imported_positions_value
        - imported_fields["unsettled_payable"]
        - imported_fields["margin_debt"]
        - imported_fields["accrued_financing_interest"]
    )
    imported_arithmetic_gap = imported_arithmetic_nlv - broker_nlv
    if abs(imported_arithmetic_gap) > cash_tol + max(value_tol, 0.01):
        blockers.append("IMPORTED_BROKER_EXPORT_BALANCE_SHEET_DOES_NOT_RECONCILE")
    if discrepancies:
        blockers.append("LOCAL_AND_IMPORTED_BROKER_VALUES_DIFFER")

    # A comparison of manually prepared snapshots does not authenticate
    # either side. No field in this report can enable actual order routing.
    matched = not blockers
    return {
        "method": "offline_broker_export_vs_local_multi_asset_ledger",
        "account_id": account_id,
        "source_claim": snap["source"],
        "snapshot_age_seconds": age,
        "max_snapshot_age_seconds": max_age,
        "decision": "MATCHED_UNVERIFIED_EXPORT" if matched else "BLOCK_RECONCILIATION",
        "matched_account_and_positions": matched,
        "eligible_for_paper_research_only": matched,
        "live_execution_enabled": False,
        "source_authentication_verified": False,
        "broker_order_margin_preview_verified": False,
        "local_net_liquidation": account["net_liquidation_calculated"],
        "imported_net_liquidation": broker_nlv,
        "imported_balance_sheet_net_liquidation": imported_arithmetic_nlv,
        "imported_balance_sheet_reconciliation_gap": imported_arithmetic_gap,
        "imported_available_funds_report_only": available_funds,
        "imported_excess_liquidity_report_only": excess_liquidity,
        "matched_position_count": len(local_by_key.keys() & broker_by_key.keys()),
        "missing_in_import": missing,
        "unexpected_in_import": unexpected,
        "differences": discrepancies,
        "block_reasons": sorted(set(blockers)),
        "warnings": [
            "This is a comparison of locally supplied JSON snapshots; actual broker origin and balances are not authenticated.",
            "Available funds and excess liquidity are imported report fields, NOT new financing authority.",
            "No cross-account cash, securities, collateral, or borrowing netting occurs.",
            "No broker orders or paper orders are submitted; an exact match never enables live trading.",
        ],
    }
