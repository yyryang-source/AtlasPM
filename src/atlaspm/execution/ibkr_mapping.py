"""Local-only IBKR contract mapping diagnostics; no margin normalization or orders.

Strictly checks a manually reviewed conid mapping against a captured Gateway
payload. Returns private diagnostic data to local_data/ only. No API traffic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

from atlaspm.execution.institutional_ledger import _timestamp


ASSET_CLASSES = {"STK": "equity", "BOND": "treasury", "OPT": "option"}
TYPE_UNITS = {"equity": "SHARES", "treasury": "FACE_USD", "option": "CONTRACTS"}
REQUIRED_OPTION = ("underlying", "right", "strike", "expiration_date", "multiplier")


def _obj(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value.strip()


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value):
        raise ValueError(f"{name} must be finite numeric data")
    return float(value)


def _conid(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("IBKR conid must be a positive integer")
    return value


def _market_value(row: Mapping[str, Any], conid: int) -> float | None:
    """Recognize documented mktValue and legacy fixture marketValue."""
    available = [name for name in ("mktValue", "marketValue") if name in row]
    if not available:
        return None
    values = [_finite(row[key], f"{conid}.{key}") for key in available]
    if len(values) == 2 and abs(values[0] - values[1]) > 0.01:
        raise ValueError(f"IBKR conid {conid} has conflicting market-value fields")
    return values[0]


def _validate_mapping_record(record: dict[str, Any]) -> dict[str, Any]:
    conid = _conid(record.get("conid"))
    kind = record.get("atlaspm_type")
    if kind not in TYPE_UNITS:
        raise ValueError(f"Mapping conid {conid} has an unsupported AtlasPM type")
    identifier = _text(record.get("identifier"), "mapping.identifier")
    asset_class = _text(record.get("ibkr_asset_class"), "mapping.ibkr_asset_class")
    if ASSET_CLASSES.get(asset_class) != kind:
        raise ValueError(f"Mapping conid {conid} asset class conflicts with declared type")
    if record.get("quantity_units") != TYPE_UNITS[kind]:
        raise ValueError(f"Mapping conid {conid} requires explicit {TYPE_UNITS[kind]} units")
    verified = record.get("manually_reviewed")
    if not isinstance(verified, bool):
        raise ValueError("manually_reviewed must be true or false")
    units_confirmed = record.get("units_confirmed")
    if not isinstance(units_confirmed, bool):
        raise ValueError("units_confirmed must be true or false")
    # Reviewed JSON is still just user input. These booleans cannot grant
    # trading, collateral, or brokerage margin privileges.
    if kind == "treasury":
        # IBKR BOND also covers non-Treasury bonds. The mapping cannot
        # infer US sovereign issuer from the BOND asset-class alone.
        issuer = record.get("issuer_confirmed")
        if not isinstance(issuer, bool):
            raise ValueError("Treasury issuer_confirmed must be boolean")
        if len(identifier) != 9 or not identifier.isalnum():
            raise ValueError("Treasury identifier must have nine CUSIP characters")
    if kind == "option":
        for field in REQUIRED_OPTION:
            if field not in record:
                raise ValueError(f"Mapped option must include {field}")
        if record["right"] not in {"call", "put"}:
            raise ValueError("Option right must be call or put")
        strike = _finite(record["strike"], "option.strike")
        multiplier = _finite(record["multiplier"], "option.multiplier")
        if strike <= 0 or multiplier <= 0 or int(multiplier) != multiplier:
            raise ValueError("Option strike and multiplier must be positive")
        _timestamp(record["expiration_date"] + "T00:00:00Z", "option.expiration_date")
        _text(record["underlying"], "option.underlying")
    return {
        "conid": conid, "atlaspm_type": kind,
        "identifier": identifier, "ibkr_asset_class": asset_class,
        "quantity_units": TYPE_UNITS[kind],
        "manually_reviewed": verified, "units_confirmed": units_confirmed,
        **({"issuer_confirmed": record["issuer_confirmed"]} if kind == "treasury" else {}),
        **({key: record[key] for key in REQUIRED_OPTION} if kind == "option" else {}),
    }


def diagnose_ibkr_contract_mapping(
    capture_data: Mapping[str, Any],
    mapping_data: Mapping[str, Any],
    *, now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Check IBKR conids vs manual identifiers; NEVER assert funding readiness."""
    capture = _obj(capture_data, "capture")
    manifest = _obj(mapping_data, "mapping")
    source = capture.get("source")
    if source not in {"synthetic_ibkr_fixture", "local_authenticated_ibkr_gateway"}:
        raise ValueError("Unsupported capture source label")
    raw = _obj(capture.get("raw"), "capture.raw")
    ledger = _obj(raw.get("USD_ledger"), "capture.raw.USD_ledger")
    usd = _obj(ledger.get("USD"), "capture.raw.USD_ledger.USD")
    settled = _finite(usd.get("settledcash"), "USD.settledcash")
    nlv = _finite(usd.get("netliquidationvalue"), "USD.netliquidationvalue")
    if nlv <= 0:
        raise ValueError("Captured USD NLV must be positive")
    account_id = _text(capture.get("account_id"), "capture.account_id")
    if manifest.get("account_id") != account_id:
        raise ValueError("Mapping account_id must match captured account_id")
    age_limit = _finite(manifest.get("max_capture_age_seconds"), "max_capture_age_seconds")
    if not 0 < age_limit <= 86400:
        raise ValueError("max_capture_age_seconds must be in (0, 86400]")
    captured_at = _timestamp(capture.get("captured_at_utc"), "captured_at_utc")
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    elapsed = (now - captured_at).total_seconds()
    if elapsed < -1:
        raise ValueError("Capture timestamp cannot be in the future")
    blockers: list[str] = []
    warnings: list[str] = []
    if elapsed > age_limit:
        blockers.append("IBKR_CAPTURE_STALE")
    accounts = _obj(raw.get("selected_account"), "capture.raw.selected_account")
    if accounts.get("accountId", accounts.get("id")) != account_id:
        blockers.append("IBKR_CAPTURE_ACCOUNT_ID_MISMATCH")
    if accounts.get("currency") != "USD":
        blockers.append("IBKR_NON_USD_BASE_ACCOUNT")
    if capture.get("session_reported_authenticated") is not True:
        blockers.append("IBKR_SESSION_NOT_REPORTED_AUTHENTICATED")
    raw_positions = raw.get("positions")
    if not isinstance(raw_positions, list):
        raise ValueError("Captured positions must be an array")
    raw_by_id: dict[int, dict[str, Any]] = {}
    for row in raw_positions:
        p = _obj(row, "IBKR position")
        conid = _conid(p.get("conid"))
        if conid in raw_by_id:
            raise ValueError(f"Duplicate IBKR conid {conid} in captured positions")
        if p.get("currency") != "USD":
            blockers.append("IBKR_POSITION_CURRENCY_UNSUPPORTED")
        raw_by_id[conid] = p
    records = manifest.get("contracts")
    if not isinstance(records, list):
        raise ValueError("Mapping contracts must be an array")
    manual_by_id: dict[int, dict[str, Any]] = {}
    mapped_identity_keys: set[tuple[str, str]] = set()
    for row in records:
        item = _validate_mapping_record(_obj(row, "mapping contract"))
        conid = item["conid"]
        if conid in manual_by_id:
            raise ValueError("Duplicate conid in mapping file")
        key = (item["atlaspm_type"], item["identifier"])
        if key in mapped_identity_keys:
            raise ValueError("Multiple conids mapped to the same AtlasPM instrument")
        mapped_identity_keys.add(key)
        manual_by_id[conid] = item
    unmapped = sorted(raw_by_id.keys() - manual_by_id.keys())
    no_position = sorted(manual_by_id.keys() - raw_by_id.keys())
    if unmapped:
        blockers.append("UNMAPPED_IBKR_CONTRACTS")
    if no_position:
        blockers.append("MAPPED_CONTRACT_NOT_HELD")
    result_positions: list[dict[str, Any]] = []
    sum_values = 0.0
    any_missing_value = False
    for conid in sorted(raw_by_id.keys() & manual_by_id.keys()):
        p = raw_by_id[conid]
        mapped = manual_by_id[conid]
        kind = mapped["atlaspm_type"]
        source_class = p.get("assetClass", p.get("secType"))
        record_blockers: list[str] = []
        if source_class != mapped["ibkr_asset_class"]:
            record_blockers.append("IBKR_ASSET_CLASS_MISMATCH")
        qty = _finite(p.get("position"), f"{conid}.position")
        if kind in {"equity", "treasury"} and qty <= 0:
            record_blockers.append("NONPOSITIVE_LONG_HOLDING")
        if kind == "option" and (qty == 0 or qty != int(qty)):
            record_blockers.append("INVALID_SIGNED_OPTION_QUANTITY")
        if not mapped["manually_reviewed"]:
            record_blockers.append("CONTRACT_IDENTITY_NOT_REVIEWED")
        if not mapped["units_confirmed"]:
            record_blockers.append("IBKR_POSITION_UNITS_NOT_CONFIRMED")
        if kind == "treasury" and not mapped["issuer_confirmed"]:
            record_blockers.append("US_TREASURY_ISSUER_NOT_CONFIRMED")
        # IBKR BOND position has historically varied unit and quote
        # conventions. FACE_USD is explicitly declared but *never* inferred.
        value = _market_value(p, conid)
        if value is None:
            record_blockers.append("IBKR_POSITION_MARKET_VALUE_MISSING")
            any_missing_value = True
        else:
            sum_values += value
            if kind in {"equity", "treasury"} and value < 0:
                record_blockers.append("NEGATIVE_LONG_MARKET_VALUE")
            if kind == "option" and value * qty < -0.01:
                record_blockers.append("OPTION_VALUE_SIGN_MISMATCH")
        blockers.extend(record_blockers)
        result_positions.append({
            "conid": conid,
            "atlaspm_identifier": mapped["identifier"],
            "atlaspm_type": kind,
            "ibkr_asset_class": source_class,
            "ibkr_position_quantity": qty,
            "declared_units": mapped["quantity_units"],
            "signed_market_value_usd": value,
            "manual_identity_reviewed_claim": mapped["manually_reviewed"],
            "unit_confirmation_claim": mapped["units_confirmed"],
            **({"issuer_confirmed_claim": mapped["issuer_confirmed"]} if kind == "treasury" else {}),
            "mapping_issues": sorted(set(record_blockers)),
            **({k: mapped[k] for k in REQUIRED_OPTION} if kind == "option" else {}),
        })
    if any_missing_value:
        warnings.append("MISSING_MARKS_PREVENT_POSITION_VALUE_AGGREGATION")
    # This is an unexplained difference, NOT a margin loan balance!
    cash_plus_marked = settled + sum_values if not any_missing_value and not unmapped else None
    unexplained_bridge = (
        nlv - cash_plus_marked if cash_plus_marked is not None else None
    )
    if unexplained_bridge is not None and abs(unexplained_bridge) > 0.01:
        warnings.append("USD_NLV_NOT_EXPLAINED_BY_SETTLED_CASH_PLUS_POSITION_MARKS")
    # IBKR's USD.interest field is a currency margin interest RATE in the
    # documented ledger. It is NOT an accrued interest debt balance.
    if "interest" in usd:
        warnings.append("IBKR_INTEREST_FIELD_IS_RATE_NOT_ACCRUED_DEBT")
    if settled < 0:
        warnings.append("NEGATIVE_SETTLED_CASH_REQUIRES_MARGIN_DEBT_RECONCILIATION")
    if "USD" not in ledger or any(
        currency not in {"USD", "BASE"} for currency in ledger
    ):
        blockers.append("IBKR_ADDITIONAL_CURRENCY_LEDGERS_REQUIRE_FX")
    mapping_ok = len(blockers) == 0
    return {
        "method": "ibkr_readonly_conid_mapping_diagnostic",
        "capture_source_claim": source,
        "account_id": account_id,
        "capture_age_seconds": elapsed,
        "capture_freshness_threshold_seconds": age_limit,
        "mapping_status": "IDENTITIES_MAPPED_REVIEW_REQUIRED" if mapping_ok else "BLOCK_MAPPING",
        "mapped_contract_count": len(result_positions),
        "unmapped_ibkr_conids": unmapped,
        "manifest_conids_not_held": no_position,
        "positions": result_positions,
        "IBKR_settledcash_field_usd": settled,
        "IBKR_netliquidationvalue_field_usd": nlv,
        "IBKR_total_available_position_marks_usd": sum_values if not any_missing_value else None,
        "unexplained_nlv_minus_settled_cash_and_marks_usd": unexplained_bridge,
        "IBKR_interest_field_interpreted_as_accrued_debt": False,
        "IBKR_financing_debt_separately_verified": False,
        "broker_treasury_collateral_verified": False,
        "IBKR_margin_or_whatif_verified": False,
        "source_authentication_independently_proven": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "mapping_block_reasons": sorted(set(blockers)),
        "warnings": sorted(set(warnings + [
            "Mappings and source labels are user-supplied, not independent broker identity proof.",
            "IBKR BOND may be corporate or sovereign: U.S. Treasury issuer and CUSIP must be independently checked before any identity mapping is trusted.",
            "IBKR BOND position units must be checked against the actual contract: no automatic face-value conversion.",
            "This diagnostic does not convert signed settledcash into margin debt, map collateral availability, or authorize trading.",
            "All financial values and full identifiers are private; keep this report inside local_data/.",
        ])),
    }


def mapping_redacted_summary(result: Mapping[str, Any], output_path: str) -> dict[str, Any]:
    """Console intentionally contains no conids, amounts, tickers or raw data."""
    account = str(result["account_id"])
    return {
        "written": output_path,
        "method": result["method"],
        "account_hint": "****" + account[-4:],
        "mapping_status": result["mapping_status"],
        "mapped_contract_count": result["mapped_contract_count"],
        "unmapped_contract_count": len(result["unmapped_ibkr_conids"]),
        "unmatched_manifest_count": len(result["manifest_conids_not_held"]),
        "mapping_block_reasons": result["mapping_block_reasons"],
        "source_authentication_independently_proven": False,
        "IBKR_financing_debt_separately_verified": False,
        "broker_treasury_collateral_verified": False,
        "IBKR_margin_or_whatif_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Local mapping diagnostics only; raw IBKR positions and dollar balances are private.",
            "Even a complete mapping does not establish actual collateral, margin buying power or permission to trade.",
        ],
    }
