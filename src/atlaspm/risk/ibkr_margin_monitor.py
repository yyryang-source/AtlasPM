"""IBKR Client Portal GET-only current-margin monitor (v0.4.10).

Only publicized /portfolio/{accountId}/summary fields are parsed. Account
risk alerts are NOT order approvals, financing statements or a broker What-If.
"""

from __future__ import annotations

from atlaspm.engine.contracts import finite_number

from datetime import datetime, timezone
from math import isfinite
import re
from typing import Any, Mapping
from urllib.parse import quote

from atlaspm.execution.ibkr_gateway import collect_ibkr_readonly_account
from atlaspm.execution.institutional_ledger import _timestamp


# The "-s" suffix is explicitly the securities segment in IBKR Web API.
SECURITIES_FIELDS = {
    "net_liquidation": "netliquidation-s",
    "equity_with_loan_value": "equitywithloanvalue-s",
    "initial_margin": "initmarginreq-s",
    "maintenance_margin": "maintmarginreq-s",
    "available_funds": "availablefunds-s",
    "excess_liquidity": "excessliquidity-s",
}
REQUIRED_FIELDS = {"total_nlv": "netliquidation", **SECURITIES_FIELDS}
OPTIONAL_FIELDS = {
    "lookahead_excess_liquidity": "lookaheadexcessliquidity-s",
    "lookahead_initial_margin": "lookaheadinitmarginreq-s",
    "lookahead_maintenance_margin": "lookaheadmaintmarginreq-s",
}
USD_TOLERANCE = 1.0

# Known exposure fields from the Gateway ledger and observed Paper schema.
# Require complete zero exposure evidence, not a sum that can hide offsets.
_FOREIGN_EXPOSURE_FIELDS = frozenset({
    "cashbalance", "cashbalancefxsegment", "settledcash", "commoditymarketvalue",
    "corporatebondsmarketvalue", "cryptocurrencyvalue", "dividends", "funds",
    "futuremarketvalue", "futureoptionmarketvalue", "futuresonlypnl",
    "issueroptionsmarketvalue", "moneyfunds", "realizedpnl", "stockmarketvalue",
    "stockoptionmarketvalue", "tbillsmarketvalue", "tbondsmarketvalue",
    "unrealizedpnl", "warrantsmarketvalue",
})
_FOREIGN_METADATA_FIELDS = frozenset({
    "acctcode", "currency", "key", "secondkey", "sessionid", "timestamp",
    "severity", "endofbundle", "exchangerate", "interest", "netliquidationvalue",
})


def _number(value: Any, name: str) -> float:
    return finite_number(value, name, invalid_message=f"{name} must be a finite JSON number")

def _foreign_ledger_diagnostics(ledger, account_id, gateway_nlv):
    """USD-only diagnostics; nonzero foreign NLV is never silently discarded.

    A matching FX translation is only a possible explanation, not proof of
    zero foreign exposure. Keep this distinct from verified financing.
    """
    diagnostics = []
    blocks = set()
    for currency in sorted(set(ledger) - {"USD", "BASE"}):
        row = ledger[currency]
        if (not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)
                or not isinstance(row, dict)
                or row.get("currency") != currency or row.get("acctcode") != account_id
                or row.get("secondkey", currency) != currency
                or type(row.get("severity")) is not int or row["severity"] != 0):
            blocks.add("FOREIGN_LEDGER_IDENTITY_OR_SCHEMA_UNVERIFIED")
            continue
        if (not _FOREIGN_EXPOSURE_FIELDS.issubset(row)
                or set(row) - _FOREIGN_EXPOSURE_FIELDS - _FOREIGN_METADATA_FIELDS):
            blocks.add("FOREIGN_LEDGER_EXPOSURE_FIELDS_INCOMPLETE_OR_UNKNOWN")
            continue
        try:
            exposure = [_number(row[key], "foreign exposure") for key in _FOREIGN_EXPOSURE_FIELDS]
            nlv = _number(row.get("netliquidationvalue"), "foreign NLV")
            rate = _number(row.get("exchangerate"), "foreign exchange rate")
            # Documented rate is base to foreign currency. Do not guess inverse.
            if rate <= 0:
                raise ValueError("Nonpositive FX rate")
            if "interest" in row:
                _number(row["interest"], "foreign interest field")
        except (ValueError, OverflowError):
            blocks.add("FOREIGN_LEDGER_NUMERIC_FIELDS_INVALID")
            continue
        if any(value != 0 for value in exposure):
            blocks.add("NONZERO_FOREIGN_LEDGER_EXPOSURE")
            state = "NONZERO_EXPOSURE"
        elif nlv != 0:
            blocks.add("FOREIGN_NLV_UNEXPLAINED")
            state = "ZERO_EXPOSURE_FIELDS_NONZERO_NLV"
        else:
            state = "ZERO_REPORTED_EXPOSURE_AND_NLV"
        diagnostics.append({
            "currency": currency, "state": state,
            "possible_base_to_foreign_nlv_translation": (
                nlv != 0 and abs(nlv / rate - gateway_nlv) <= USD_TOLERANCE
            ),
        })
    if "BASE" in ledger and set(ledger) - {"USD", "BASE"}:
        base = ledger["BASE"]
        try:
            if not isinstance(base, dict) or base.get("currency") not in {"BASE", "USD"} or base.get("acctcode") != account_id:
                raise ValueError("BASE identity")
            for field in ("netliquidationvalue", "settledcash", "cashbalance"):
                if abs(_number(base.get(field), "BASE field") -
                       _number(ledger["USD"].get(field), "USD field")) > USD_TOLERANCE:
                    blocks.add("BASE_VS_USD_LEDGER_MISMATCH")
        except (ValueError, OverflowError):
            blocks.add("BASE_LEDGER_UNVERIFIED")
    return diagnostics, sorted(blocks)


def _metric(
    summary: Mapping[str, Any], key: str, *,
    fixture_mode: bool, at: datetime,
) -> tuple[float, datetime]:
    row = summary.get(key)
    if not isinstance(row, dict):
        raise ValueError(f"Required IBKR margin field is missing or invalid: {key}")
    if row.get("isNull") is not False:
        raise ValueError(f"IBKR margin field was null: {key}")
    if row.get("severity") != 0:
        raise ValueError(f"IBKR margin field has a nonzero or missing severity: {key}")
    amount = _number(row.get("amount"), key)
    currency = row.get("currency")
    # The Portfolio summary API can return an IBKR numeric currency code
    # (e.g. 1.1). This is NOT a USD ISO currency code or FX rate.
    if isinstance(currency, str) and currency not in {"USD", ""}:
        raise ValueError(f"Explicitly non-USD margin field: {key}")
    stamp = row.get("timestamp")
    if fixture_mode and stamp == "SYNTHETIC_NOW":
        instant = at
    elif (
        not isinstance(stamp, bool)
        and isinstance(stamp, (int, float))
        and isfinite(stamp)
        and 946684800000 <= stamp <= 4102444800000
    ):
        instant = datetime.fromtimestamp(stamp / 1000.0, tz=timezone.utc)
    else:
        raise ValueError(f"IBKR field requires epoch-millisecond timestamp: {key}")
    return amount, instant


def normalize_ibkr_margin_summary(
    raw_summary: Mapping[str, Any], capture: Mapping[str, Any], *,
    fixture_mode: bool = False, now_utc: datetime | None = None,
    max_field_age_seconds: int = 600, min_buffer_ratio: float = 0.15,
) -> dict[str, Any]:
    """Normalize broker-observed margin *fields*, NOT debt or trade authority."""
    if not isinstance(raw_summary, dict) or not isinstance(capture, dict):
        raise ValueError("Summary and capture must be JSON objects")
    if (isinstance(max_field_age_seconds, bool) or not isinstance(max_field_age_seconds, int)
            or not 0 < max_field_age_seconds <= 3600):
        raise ValueError("max_field_age_seconds must be integer in [1,3600]")
    ratio_floor = _number(min_buffer_ratio, "min_buffer_ratio")
    if not 0 <= ratio_floor <= 1:
        raise ValueError("min_buffer_ratio must be in [0,1]")
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    captured = _timestamp(capture.get("captured_at_utc"), "captured_at_utc")
    account_id = capture.get("account_id")
    if not isinstance(account_id, str) or not account_id:
        raise ValueError("Captured account identity missing")
    if capture.get("source") != (
        "synthetic_ibkr_fixture" if fixture_mode else "local_authenticated_ibkr_gateway"
    ):
        raise ValueError("Capture source does not match collection mode")
    code = raw_summary.get("accountcode")
    if not isinstance(code, dict) or code.get("isNull") is not False or code.get("value") != account_id:
        raise ValueError("IBKR portfolio summary account code mismatch")
    raw = capture.get("raw")
    if not isinstance(raw, dict):
        raise ValueError("Capture has no raw broker data")
    account = raw.get("selected_account")
    if not isinstance(account, dict) or account.get("currency") != "USD":
        raise ValueError("Only USD-base accounts supported")
    if account.get("accountId", account.get("id")) != account_id:
        raise ValueError("Captured account selector mismatch")
    ledger = raw.get("USD_ledger")
    if not isinstance(ledger, dict) or not all(isinstance(key, str) for key in ledger):
        raise ValueError("Invalid IBKR ledger object")
    usd = ledger.get("USD")
    if not isinstance(usd, dict):
        raise ValueError("USD ledger missing")
    gateway_nlv = _number(usd.get("netliquidationvalue"), "gateway USD NLV")
    foreign_diagnostics, foreign_blocks = _foreign_ledger_diagnostics(ledger, account_id, gateway_nlv)

    metrics: dict[str, float] = {}
    stamps: list[datetime] = []
    for label, key in REQUIRED_FIELDS.items():
        metrics[label], stamp = _metric(
            raw_summary, key, fixture_mode=fixture_mode, at=now
        )
        stamps.append(stamp)

    extra: dict[str, float] = {}
    for label, key in OPTIONAL_FIELDS.items():
        if key in raw_summary:
            extra[label], stamp = _metric(
                raw_summary, key, fixture_mode=fixture_mode, at=now
            )
            stamps.append(stamp)
    if "netliquidation-c" in raw_summary:
        extra["commodities_nlv"], stamp = _metric(
            raw_summary, "netliquidation-c", fixture_mode=fixture_mode, at=now
        )
        stamps.append(stamp)

    age = max((now - stamp).total_seconds() for stamp in stamps)
    earliest = min(stamps)
    latest = max(stamps)
    span = (latest - earliest).total_seconds()
    integrity: list[str] = list(foreign_blocks)
    alerts: list[str] = []
    if age > max_field_age_seconds:
        integrity.append("MARGIN_DATA_STALE")
    if (latest - now).total_seconds() > 5:
        integrity.append("MARGIN_DATA_FUTURE_DATED")
    if span > 120:
        integrity.append("MARGIN_FIELD_TIMESTAMPS_DIVERGE")
    if abs((captured - latest).total_seconds()) > 120:
        integrity.append("MARGIN_CAPTURE_SNAPSHOT_SKEW")
    if capture.get("session_reported_authenticated") is not True:
        integrity.append("IBKR_GATEWAY_AUTHENTICATION_MISSING")
    if fixture_mode is False and capture.get("gateway_tls_certificate_validated") is not True:
        integrity.append("IBKR_TLS_VERIFICATION_MISSING")
    if "commodities_nlv" in extra and abs(extra["commodities_nlv"]) > USD_TOLERANCE:
        integrity.append("NONZERO_COMMODITIES_SEGMENT_NOT_SUPPORTED")
    if abs(metrics["total_nlv"] - gateway_nlv) > USD_TOLERANCE:
        integrity.append("PORTFOLIO_NLV_VS_LEDGER_MISMATCH")
    if abs(metrics["total_nlv"] - metrics["net_liquidation"]) > USD_TOLERANCE:
        integrity.append("SECURITIES_NLV_VS_TOTAL_MISMATCH")
    if metrics["net_liquidation"] <= 0:
        integrity.append("NONPOSITIVE_NLV")
    if metrics["equity_with_loan_value"] < 0:
        integrity.append("NEGATIVE_EQUITY_WITH_LOAN_VALUE")
    if metrics["initial_margin"] < 0 or metrics["maintenance_margin"] < 0:
        integrity.append("NEGATIVE_MARGIN_REQUIREMENT")
    available_gap = (
        metrics["available_funds"] - metrics["equity_with_loan_value"]
        + metrics["initial_margin"]
    )
    excess_gap = (
        metrics["excess_liquidity"] - metrics["equity_with_loan_value"]
        + metrics["maintenance_margin"]
    )
    if abs(available_gap) > USD_TOLERANCE:
        integrity.append("AVAILABLE_FUNDS_NOT_RECONCILED")
    if abs(excess_gap) > USD_TOLERANCE:
        integrity.append("EXCESS_LIQUIDITY_NOT_RECONCILED")
    if metrics["available_funds"] < 0:
        alerts.append("AVAILABLE_FUNDS_NEGATIVE")
    if metrics["excess_liquidity"] <= 0:
        alerts.append("EXCESS_LIQUIDITY_DEPLETED")
    cushion = (
        metrics["excess_liquidity"] / metrics["net_liquidation"]
        if metrics["net_liquidation"] > 0 else None
    )
    if cushion is not None and cushion < ratio_floor:
        alerts.append("MARGIN_BUFFER_BELOW_POLICY_FLOOR")
    if "lookahead_excess_liquidity" in extra and extra["lookahead_excess_liquidity"] <= 0:
        alerts.append("LOOKAHEAD_EXCESS_LIQUIDITY_DEPLETED")

    state = (
        "BLOCKED_DATA" if integrity else
        "CRITICAL" if "EXCESS_LIQUIDITY_DEPLETED" in alerts or
                      "LOOKAHEAD_EXCESS_LIQUIDITY_DEPLETED" in alerts else
        "WATCH" if alerts else "NORMAL"
    )
    return {
        "method": "ibkr_gateway_portfolio_summary_margin_monitor",
        "source": capture["source"],
        "account_id": account_id,
        "monitor_state": state,
        "data_integrity_block_reasons": sorted(set(integrity)),
        "risk_alerts": sorted(set(alerts)),
        "margin_summary_usd_claim": metrics,
        "foreign_ledger_diagnostics": foreign_diagnostics,
        "foreign_ledger_risk_clear": not foreign_blocks,
        "account_base_currency": "USD",
        "optional_margin_summary_usd_claim": extra,
        "formula_differences_usd": {
            "available_funds": available_gap,
            "excess_liquidity": excess_gap,
            "total_nlv_vs_gateway_usd_nlv": metrics["total_nlv"] - gateway_nlv,
        },
        "margin_field_max_age_seconds": age,
        "margin_field_timestamp_span_seconds": span,
        "margin_capture_skew_seconds": abs((captured - latest).total_seconds()),
        "excess_liquidity_to_nlv": cushion,
        "minimum_buffer_ratio": ratio_floor,
        "broker_observed_summary_fields": not fixture_mode,
        "IBKR_financing_debt_separately_verified": False,
        "broker_treasury_collateral_verified": False,
        "IBKR_order_bound_whatif_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Read-only GET account summary is not an order-specific broker What-If.",
            "Current margin fields are observed, but no independent reconciliation of financing principal, fees, or accrued interest was performed.",
            "USD.interest in the Gateway ledger is an interest rate, not a loan liability; signed settledcash is not verified debt.",
            "This monitor does not verify per-security collateral haircuts or options permissions.",
            "A NORMAL monitoring state never authorizes trading or proves future margin sufficiency.",
            "Full records must remain under Git-ignored local_data/.",
        ],
    }


def collect_ibkr_margin_monitor(
    transport: Any, *, account_id: str, fixture_mode: bool = False,
    max_field_age_seconds: int = 600, min_buffer_ratio: float = 0.15,
) -> dict[str, Any]:
    """Recheck auth + selected account in the same transport session."""
    capture = collect_ibkr_readonly_account(
        transport, account_id=account_id, fixture_mode=fixture_mode
    )
    summary = transport.get(
        f"/portfolio/{quote(account_id, safe='')}/summary"
    )
    report = normalize_ibkr_margin_summary(
        summary, capture, fixture_mode=fixture_mode,
        max_field_age_seconds=max_field_age_seconds,
        min_buffer_ratio=min_buffer_ratio,
    )
    # The raw payload is sensitive and never returned to stdout.
    report["raw_portfolio_summary"] = summary
    return report


def redacted_margin_console(report: Mapping[str, Any], output: str) -> dict[str, Any]:
    account = str(report["account_id"])
    return {
        "written": output,
        "method": report["method"],
        "account_hint": "****" + account[-4:],
        "source": report["source"],
        "monitor_state": report["monitor_state"],
        "data_integrity_block_reasons": report["data_integrity_block_reasons"],
        "risk_alerts": report["risk_alerts"],
        "foreign_ledger_diagnostics": report["foreign_ledger_diagnostics"],
        "foreign_ledger_risk_clear": report["foreign_ledger_risk_clear"],
        "broker_observed_summary_fields": report["broker_observed_summary_fields"],
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Local-only account diagnostics; no private dollar amounts or complete account IDs in stdout.",
            "Margin monitoring is not broker loan reconciliation or trading authorization.",
        ],
    }
