"""Read-only, multi-asset account ledger for institutional research.

All data is supplied via a local JSON snapshot. No API calls, trades, broker
buying-power calculation, collateral creation or option exercise occurs here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _money(value: Any, label: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if nonnegative and number < 0:
        raise ValueError(f"{label} must be nonnegative")
    return number


def _positive(value: Any, label: str) -> float:
    result = _money(value, label)
    if result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value.strip()


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO 8601 timezone-aware string")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} invalid timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{label} requires timezone")
    return dt.astimezone(timezone.utc)


def _position(raw: Any, account_id: str) -> dict[str, Any]:
    p = _object(raw, "position")
    kind = p.get("type")
    if kind not in {"equity", "treasury", "option"}:
        raise ValueError("position.type must be equity, treasury, or option")
    identifier = _identifier(p.get("identifier"), "position.identifier")
    key = f"{account_id}/{kind}/{identifier}"
    mark_age = _money(p.get("mark_age_seconds"), f"{key}.mark_age_seconds", nonnegative=True)
    source = _identifier(p.get("mark_source"), f"{key}.mark_source")

    if kind == "equity":
        shares = _positive(p.get("shares"), f"{key}.shares")
        mark = _money(p.get("mark_price"), f"{key}.mark_price", nonnegative=True)
        value = shares * mark
        result = {
            "type": kind, "identifier": identifier, "shares": shares,
            "mark_price": mark, "market_value": value,
            "gross_notional": value,
        }

    elif kind == "treasury":
        face = _positive(p.get("face_value"), f"{key}.face_value")
        clean = _money(p.get("clean_price_per_100"), f"{key}.clean_price_per_100", nonnegative=True)
        accrued_per_100 = _money(
            p.get("accrued_interest_per_100"), f"{key}.accrued_interest_per_100",
            nonnegative=True,
        )
        if clean <= 0:
            raise ValueError(f"{key}.clean_price_per_100 must be positive")
        maturity = p.get("maturity_date")
        try:
            date_maturity = datetime.strptime(maturity, "%Y-%m-%d").date()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key}.maturity_date invalid") from exc
        coupon_rate = _money(p.get("coupon_rate"), f"{key}.coupon_rate", nonnegative=True)
        if coupon_rate > 1:
            raise ValueError(f"{key}.coupon_rate must be a fraction <=1")
        clean_value = face * clean / 100
        accrued_value = face * accrued_per_100 / 100
        value = clean_value + accrued_value
        duration = p.get("modified_duration")
        dv01: float | None = None
        if duration is not None:
            duration = _money(duration, f"{key}.modified_duration", nonnegative=True)
            dv01 = value * duration * 0.0001
        collateral = p.get("collateral", {"status": "UNKNOWN"})
        data = _object(collateral, f"{key}.collateral")
        status = data.get("status")
        if status not in {"ELIGIBLE", "INELIGIBLE", "UNKNOWN"}:
            raise ValueError(f"{key}.collateral.status invalid")
        credited = 0.0
        if status == "ELIGIBLE":
            if data.get("source") != "broker":
                raise ValueError(f"{key}: only broker-confirmed Treasury credits are accepted")
            credited = _money(
                data.get("broker_recognized_value"),
                f"{key}.collateral.broker_recognized_value", nonnegative=True
            )
            if credited > value + max(0.01, value * 1e-8):
                raise ValueError(f"{key}: collateral credit cannot exceed marked Treasury value")
        elif "broker_recognized_value" in data:
            raise ValueError(f"{key}: non-eligible Treasury cannot claim recognized collateral value")
        result = {
            "type": kind, "identifier": identifier,
            "maturity_date": date_maturity.isoformat(),
            "coupon_rate": coupon_rate, "face_value": face,
            "clean_price_per_100": clean, "accrued_interest_per_100": accrued_per_100,
            "clean_market_value": clean_value,
            "accrued_interest_in_dirty_value": accrued_value,
            "market_value": value, "gross_notional": value,
            "modified_duration": duration, "dv01_usd_per_bp": dv01,
            "broker_recognized_collateral_value": credited,
            "collateral_status": status,
            "collateral_credit_is_cash": False,
        }

    else:
        contracts = _money(p.get("contracts"), f"{key}.contracts")
        if contracts == 0 or contracts != int(contracts):
            raise ValueError(f"{key}.contracts must be a nonzero signed integer")
        multiplier = _positive(p.get("multiplier"), f"{key}.multiplier")
        if multiplier != int(multiplier):
            raise ValueError(f"{key}.multiplier must be a positive integer")
        mark = _money(p.get("mark_premium"), f"{key}.mark_premium", nonnegative=True)
        right = p.get("right")
        if right not in {"put", "call"}:
            raise ValueError(f"{key}.right must be put or call")
        underlying = _identifier(p.get("underlying"), f"{key}.underlying")
        strike = _positive(p.get("strike"), f"{key}.strike")
        expiry = p.get("expiration_date")
        try:
            expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key}.expiration_date invalid") from exc
        value = contracts * multiplier * mark
        result = {
            "type": kind, "identifier": identifier, "underlying": underlying,
            "right": right, "strike": strike, "expiration_date": expiry_date.isoformat(),
            "contracts": int(contracts), "multiplier": int(multiplier),
            "mark_premium": mark,
            "market_value": value, "gross_notional": abs(value),
            "short_option_liability": max(-value, 0.0),
            "requires_broker_option_margin_validation": contracts < 0,
        }
    result["mark_source"] = source
    result["mark_age_seconds"] = mark_age
    return result


def build_institutional_ledger(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reconcile account equity using supplied cash, debt, mark and liability data.

    A zero reconciliation error is *not* a broker-independent valuation audit.
    Broker-confirmed Treasury collateral is reported, not added to equity/cash.
    """
    raw = _object(payload, "payload")
    valuation_at = _timestamp(raw.get("valuation_at"), "valuation_at")
    max_age = _positive(raw.get("max_mark_age_seconds"), "max_mark_age_seconds")
    tolerance = _money(raw.get("reconciliation_tolerance", 0.01),
                       "reconciliation_tolerance", nonnegative=True)
    raw_accounts = _array(raw.get("accounts"), "accounts")
    if not raw_accounts:
        raise ValueError("At least one account is required")
    seen_accounts: set[str] = set()
    reports: list[dict[str, Any]] = []
    total = defaultdict(float)

    for account_data in raw_accounts:
        account = _object(account_data, "account")
        account_id = _identifier(account.get("account_id"), "account.account_id")
        if account_id in seen_accounts:
            raise ValueError(f"Duplicate account_id: {account_id}")
        seen_accounts.add(account_id)
        kind = account.get("account_type")
        if kind not in {"cash", "margin"}:
            raise ValueError("account_type must be cash or margin")
        currency = account.get("currency")
        if currency != "USD":
            raise ValueError("v0.4.1 ledger supports USD accounts only; FX translation is future work")
        cash = _money(account.get("settled_cash"), "settled_cash")
        unsettled_receivable = _money(
            account.get("unsettled_receivable", 0.0), "unsettled_receivable",
            nonnegative=True,
        )
        unsettled_payable = _money(
            account.get("unsettled_payable", 0.0), "unsettled_payable",
            nonnegative=True,
        )
        margin_debt = _money(account.get("margin_debt"), "margin_debt", nonnegative=True)
        accrued_funding = _money(
            account.get("accrued_financing_interest", 0.0),
            "accrued_financing_interest", nonnegative=True,
        )
        if kind == "cash" and cash < 0:
            raise ValueError("Cash accounts cannot have negative settled cash")
        if kind == "cash" and (margin_debt > 0 or accrued_funding > 0):
            raise ValueError("Cash accounts cannot contain margin debt or accrued margin financing")
        account_as_of = _timestamp(account.get("snapshot_at"), "account.snapshot_at")
        age = (valuation_at - account_as_of).total_seconds()
        if age < -1:
            raise ValueError("Broker snapshot cannot be in the future of valuation_at")
        raw_positions = _array(account.get("positions"), "account.positions")
        positions = [_position(p, account_id) for p in raw_positions]
        if kind == "cash" and any(
            p["type"] == "option" and p["contracts"] < 0 for p in positions
        ):
            raise ValueError("Cash account cannot include short option liabilities in this prototype")
        seen_instruments: set[tuple[str, str]] = set()
        for pos in positions:
            key = (pos["type"], pos["identifier"])
            if key in seen_instruments:
                raise ValueError(f"Duplicate instrument in account {account_id}: {key}")
            seen_instruments.add(key)
        positions_value = sum(p["market_value"] for p in positions)
        gross_notional = sum(p["gross_notional"] for p in positions)
        assets = cash + unsettled_receivable + positions_value
        liabilities = unsettled_payable + margin_debt + accrued_funding
        net_liquidation = assets - liabilities
        if net_liquidation <= 0:
            raise ValueError(f"Account {account_id} has no positive net liquidation value")
        reported_nlv = account.get("broker_reported_net_liquidation")
        discrepancy: float | None = None
        if reported_nlv is not None:
            reported_nlv = _money(
                reported_nlv, "broker_reported_net_liquidation",
            )
            discrepancy = net_liquidation - reported_nlv
        mismatched = discrepancy is None or abs(discrepancy) > tolerance
        stale_instruments = [
            p["identifier"] for p in positions if p["mark_age_seconds"] > max_age
        ]
        stale_broker_snapshot = age > max_age
        unverified_collateral = [
            p["identifier"] for p in positions
            if p["type"] == "treasury" and p["collateral_status"] != "ELIGIBLE"
        ]
        broker_credit = sum(
            p.get("broker_recognized_collateral_value", 0.0) for p in positions
        )
        alerts: list[str] = []
        if mismatched:
            alerts.append("BROKER_NET_LIQUIDATION_NOT_RECONCILED")
        if stale_instruments:
            alerts.append("INSTRUMENT_MARK_STALE")
        if stale_broker_snapshot:
            alerts.append("BROKER_ACCOUNT_SNAPSHOT_STALE")
        if unverified_collateral:
            alerts.append("TREASURY_COLLATERAL_UNVERIFIED_OR_INELIGIBLE")
        if any(
            p["type"] == "option" and p["requires_broker_option_margin_validation"]
            for p in positions
        ):
            alerts.append("SHORT_OPTIONS_REQUIRE_BROKER_MARGIN_VERIFICATION")
        report = {
            "account_id": account_id, "account_type": kind, "currency": currency,
            "snapshot_at": account_as_of.isoformat(),
            "snapshot_age_seconds": age, "settled_cash": cash,
            "unsettled_receivable": unsettled_receivable,
            "unsettled_payable": unsettled_payable,
            "margin_debt": margin_debt,
            "accrued_financing_interest": accrued_funding,
            "positions_marked_market_value": positions_value,
            "gross_marked_notional": gross_notional,
            "net_liquidation_calculated": net_liquidation,
            "broker_reported_net_liquidation": reported_nlv,
            "reconciliation_difference": discrepancy,
            "is_reconciled": not mismatched,
            "account_ledger_ready_for_risk_review": not alerts,
            "broker_recognized_treasury_collateral_report_only": broker_credit,
            "unverified_treasury_identifiers": unverified_collateral,
            "stale_instrument_identifiers": stale_instruments,
            "alerts": alerts,
            "positions": positions,
        }
        reports.append(report)
        for key, val in {
            "net_liquidation": net_liquidation,
            "settled_cash": cash,
            "positions_marked_market_value": positions_value,
            "unsettled_receivable": unsettled_receivable,
            "unsettled_payable": unsettled_payable,
            "margin_debt": margin_debt,
            "accrued_financing_interest": accrued_funding,
            "broker_recognized_treasury_collateral_report_only": broker_credit,
        }.items():
            total[key] += val

    return {
        "method": "read_only_multi_asset_cash_margin_ledger",
        "valuation_at": valuation_at.isoformat(),
        "max_mark_age_seconds": max_age,
        "reconciliation_tolerance_usd": tolerance,
        "accounts": reports,
        "consolidated": {
            **dict(total),
            "account_count": len(reports),
            "accounts_ready_for_risk_review": sum(
                int(account["account_ledger_ready_for_risk_review"]) for account in reports
            ),
            "no_cross_account_collateral_netting": True,
        },
        "warnings": [
            "Read-only marks, not broker-authenticated snapshots or verified executable prices.",
            "Treasury dirty value includes accrued interest; do not add accrued coupon again to cash.",
            "Reported broker-recognized collateral is informational and NEVER included in cash or net liquidation.",
            "Treasury DV01 uses first-order duration approximation only, not a full yield curve repricing.",
            "Short option market liability is not the same as broker margin requirement or defined max loss.",
            "No actual cash settlement, option exercise, coupon booking or order execution is performed.",
            "Missing broker net liquidation, stale marks, or unsupported currencies block risk-review readiness.",
        ],
    }
