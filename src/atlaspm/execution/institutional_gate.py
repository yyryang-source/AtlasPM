"""Paper-intent institutional risk gateway; never creates broker orders.

Pure, deterministic control plane for future PMS/RMS integration. All broker
snapshots and previews are inputs provided by an adapter, not fetched here.
"""

from __future__ import annotations

from atlaspm.engine.contracts import validated_object

from atlaspm.engine.contracts import finite_number

from math import isfinite
from typing import Any, Mapping


EXPOSURE_ADDS = {"ADD_EQUITY", "BUY_TREASURY"}
RISK_REDUCTIONS = {"REDUCE_EQUITY", "REPAY_MARGIN"}
HEDGES = {"BUY_PROTECTIVE_PUT", "OPEN_PUT_SPREAD", "OPEN_COLLAR"}
VALID_ACTIONS = EXPOSURE_ADDS | RISK_REDUCTIONS | HEDGES | {"HOLD"}
RISK_STATES = ("NORMAL", "WATCH", "DE_RISK", "CRITICAL", "HALT")


def _object(value: object, label: str) -> Mapping[str, Any]:
    return validated_object(value, label, invalid_message=f"{label} must be a JSON object")

def _number(src: Mapping[str, Any], key: str, *, lo: float = 0.0, maxval: float | None = None) -> float:
    return finite_number(src.get(key), key, minimum=lo, maximum=maxval,
    invalid_message=f"{key} must be a finite number",
    minimum_message=f"{key} is outside the valid range",
    maximum_message=f"{key} is outside the valid range")

def _bool(src: Mapping[str, Any], key: str) -> bool:
    value = src.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false")
    return value


def _policy_values(policy: Mapping[str, Any]) -> dict[str, float]:
    for key in (
        "max_data_age_seconds", "max_gross_leverage",
        "max_stress_loss_fraction", "min_excess_liquidity_fraction",
        "max_single_order_fraction", "max_option_premium_fraction",
    ):
        _number(policy, key)
    watch = _number(policy, "watch_drawdown_fraction", maxval=1)
    reduce = _number(policy, "reduce_drawdown_fraction", maxval=1)
    critical = _number(policy, "critical_drawdown_fraction", maxval=1)
    if not 0 < watch < reduce < critical < 1:
        raise ValueError("drawdown policy thresholds must be strictly increasing")
    if policy["max_data_age_seconds"] == 0:
        raise ValueError("max_data_age_seconds must be positive")
    if policy["max_gross_leverage"] < 1 or policy["max_gross_leverage"] > 4:
        raise ValueError("max_gross_leverage must be between 1 and 4")
    for key in (
        "max_stress_loss_fraction", "min_excess_liquidity_fraction",
        "max_single_order_fraction", "max_option_premium_fraction"
    ):
        if policy[key] > 1:
            raise ValueError(f"{key} must be at most 1")
    return {key: float(value) for key, value in policy.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)}


def assess_institutional_intent(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate an order *proposal*; returns no executable orders or credentials.

    Real broker buying power and margin must come from authoritative broker
    snapshots and a separate current What-If preview. The API is not called.
    Unknown or old data blocks exposure-increasing and hedge intents.
    """
    top = _object(payload, "payload")
    account = _object(top.get("account"), "account")
    observed = _object(top.get("risk_observation"), "risk_observation")
    intent = _object(top.get("intent"), "intent")
    policy = _object(top.get("policy"), "policy")
    limits = _policy_values(policy)
    account_type = account.get("type")
    if account_type not in ("cash", "margin"):
        raise ValueError("account.type must be cash or margin")
    action = intent.get("action")
    if action not in VALID_ACTIONS:
        raise ValueError("Unknown institutional action")
    nlv = _number(account, "net_liquidation", lo=0)
    if nlv <= 0:
        raise ValueError("net_liquidation must be positive")
    margin_debt = _number(account, "margin_debt")
    excess = _number(account, "excess_liquidity", lo=-float("inf"))
    _number(account, "available_funds", lo=-float("inf"))
    account_age = _number(account, "data_age_seconds")
    reconciled = _bool(account, "reconciled")
    drawdown = _number(observed, "current_drawdown_fraction", maxval=1)
    stress = _number(observed, "pro_forma_stress_loss_fraction", maxval=10)
    gross = _number(observed, "pro_forma_gross_leverage", maxval=20)
    notional = _number(intent, "notional")
    quote_age = _number(top.get("market", {}), "oldest_quote_age_seconds")

    blockers: list[str] = []
    warnings: list[str] = []
    state = "NORMAL"
    if not reconciled:
        blockers.append("ACCOUNT_NOT_RECONCILED")
    if account_age > limits["max_data_age_seconds"]:
        blockers.append("BROKER_SNAPSHOT_STALE")
    if quote_age > limits["max_data_age_seconds"]:
        blockers.append("MARKET_DATA_STALE")
    if account_type == "cash" and margin_debt > 1e-8:
        blockers.append("CASH_ACCOUNT_HAS_MARGIN_DEBT")
    if blockers:
        state = "HALT"
    elif excess < 0 or drawdown >= limits["critical_drawdown_fraction"]:
        state = "CRITICAL"
        warnings.append("REQUIRES_URGENT_RISK_REVIEW")
    elif drawdown >= limits["reduce_drawdown_fraction"]:
        state = "DE_RISK"
    elif drawdown >= limits["watch_drawdown_fraction"] or excess / nlv < limits["min_excess_liquidity_fraction"] * 1.5:
        state = "WATCH"

    # Collateral credits are recognized ONLY when a broker explicitly reports
    # eligibility and monetary credit; unknown/ineligible is credited as zero.
    collateral = top.get("treasury_collateral", [])
    if not isinstance(collateral, list):
        raise ValueError("treasury_collateral must be a list")
    verified_collateral_value = 0.0
    unknown_collateral = 0
    for i, item in enumerate(collateral):
        data = _object(item, f"treasury_collateral[{i}]")
        eligible = data.get("broker_eligible")
        if eligible is not True:
            unknown_collateral += 1
            continue
        if data.get("source") != "broker":
            unknown_collateral += 1
            continue
        if _number(data, "quote_age_seconds") > limits["max_data_age_seconds"]:
            unknown_collateral += 1
            continue
        credit = data.get("broker_recognized_collateral_value")
        if isinstance(credit, bool) or not isinstance(credit, (int, float)) or not isfinite(credit) or credit < 0:
            unknown_collateral += 1
            continue
        verified_collateral_value += float(credit)

    # A valid preview is necessary for ANY proposed real transaction;
    # even a reduction must be reconciled with the account and permissions.
    preview_verified = False
    if action != "HOLD":
        preview = top.get("broker_preview")
        if not isinstance(preview, dict):
            blockers.append("BROKER_WHATIF_PREVIEW_MISSING")
        else:
            try:
                preview_verified = (
                    _bool(preview, "accepted")
                    and _number(preview, "age_seconds") <= limits["max_data_age_seconds"]
                )
                if not preview_verified:
                    blockers.append("BROKER_WHATIF_NOT_ACCEPTED_OR_STALE")
                after_excess = _number(preview, "excess_liquidity_after", lo=-float("inf"))
                after_funds = _number(preview, "available_funds_after", lo=-float("inf"))
                if action in EXPOSURE_ADDS | HEDGES and (after_excess / nlv < limits["min_excess_liquidity_fraction"] or after_funds < 0):
                    blockers.append("INSUFFICIENT_POSTTRADE_BROKER_LIQUIDITY")
                if account_type == "cash" and _number(preview, "margin_debt_after") > 1e-8:
                    blockers.append("CASH_ACCOUNT_BORROWING_PROHIBITED")
            except ValueError:
                blockers.append("BROKER_WHATIF_MALFORMED")
    if action in EXPOSURE_ADDS:
        if state != "NORMAL":
            blockers.append("RISK_STATE_BLOCKS_ADDITIONS")
        if stress > limits["max_stress_loss_fraction"]:
            blockers.append("STRESS_LOSS_BUDGET_EXCEEDED")
        if gross > limits["max_gross_leverage"]:
            blockers.append("GROSS_LEVERAGE_BUDGET_EXCEEDED")
        if notional / nlv > limits["max_single_order_fraction"]:
            blockers.append("ORDER_NOTIONAL_LIMIT_EXCEEDED")

    uses_treasury = intent.get("relies_on_treasury_collateral", False)
    if not isinstance(uses_treasury, bool):
        raise ValueError("relies_on_treasury_collateral must be boolean")
    if uses_treasury:
        if unknown_collateral:
            blockers.append("TREASURY_COLLATERAL_UNVERIFIED")
        required_credit = _number(intent, "required_treasury_credit")
        if verified_collateral_value < required_credit:
            blockers.append("TREASURY_COLLATERAL_INSUFFICIENT")
        if action == "HOLD":
            warnings.append("COLLATERAL_ANALYSIS_ONLY_NOT_A_TRADE")

    if action == "REPAY_MARGIN" and account_type != "margin":
        blockers.append("NO_MARGIN_LOAN_FOR_CASH_ACCOUNT")
    if action in HEDGES:
        option = top.get("option_hedge")
        if not isinstance(option, dict):
            blockers.append("OPTION_QUOTE_AND_CONTRACT_MISSING")
        else:
            try:
                if not _bool(option, "trading_permission"):
                    blockers.append("OPTIONS_PERMISSION_MISSING")
                if _number(option, "quote_age_seconds") > limits["max_data_age_seconds"]:
                    blockers.append("OPTION_CHAIN_STALE")
                premium = _number(option, "premium_and_fees")
                if premium / nlv > limits["max_option_premium_fraction"]:
                    blockers.append("OPTION_PREMIUM_BUDGET_EXCEEDED")
                if action == "OPEN_COLLAR" and not _bool(option, "short_call_is_covered"):
                    blockers.append("UNCOVERED_SHORT_CALL_NOT_ALLOWED")
                if action in ("OPEN_COLLAR", "OPEN_PUT_SPREAD") and not _bool(option, "spread_max_loss_bounded"):
                    blockers.append("UNBOUNDED_OPTION_SPREAD_RISK")
            except ValueError:
                blockers.append("OPTION_CONTRACT_OR_QUOTE_MALFORMED")
        if state in ("CRITICAL", "HALT"):
            blockers.append("EMERGENCY_STATE_REQUIRES_MANUAL_HEDGE_REVIEW")

    if action in RISK_REDUCTIONS and state in ("CRITICAL", "HALT"):
        # Explicitly allow a human to review risk reduction, not submission.
        warnings.append("REDUCTION_REQUIRES_OPERATOR_REVIEW")
        blockers.append("EMERGENCY_STATE_BLOCKS_AUTOMATED_SUBMISSION")
    if action == "HOLD":
        # No order is requested; report incident status without inventing a trade.
        blockers = [b for b in blockers if b in {
            "ACCOUNT_NOT_RECONCILED", "BROKER_SNAPSHOT_STALE",
            "MARKET_DATA_STALE", "CASH_ACCOUNT_HAS_MARGIN_DEBT"
        }]
    # A paper-intent approval is NEVER authorization for live trading.
    approved = not blockers and action != "HOLD"
    suggested = (
        ["FREEZE_AUTOMATED_ORDER_SUBMISSION", "RECONCILE_ACCOUNT_AND_ORDER_STATUS"]
        if state == "HALT" else
        ["BLOCK_NEW_EXPOSURE", "ESCALATE_MARGIN_AND_LIQUIDITY_REVIEW"]
        if state == "CRITICAL" else
        ["REDUCE_BORROWING_REVIEW", "EVALUATE_PROTECTIVE_HEDGE"]
        if state == "DE_RISK" else
        ["PAUSE_LEVERAGE_ADDITIONS", "WATCH_MARGIN_BUFFER"]
        if state == "WATCH" else
        ["NORMAL_RISK_MONITORING"]
    )
    return {
        "system": "AtlasPM institutional policy preflight prototype",
        "risk_state": state,
        "proposed_action": action,
        "decision": "ALLOW_PAPER_INTENT" if approved else ("NO_ORDER_REQUESTED" if action == "HOLD" else "BLOCK"),
        "eligible_for_paper_intent": approved,
        "live_execution_enabled": False,
        "broker_preview_verified": preview_verified,
        "block_reasons": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "suggested_operator_actions": suggested,
        "broker_recognized_treasury_collateral_value": verified_collateral_value,
        "unverified_or_ineligible_treasury_positions": unknown_collateral,
        "disclaimer": "Read-only policy contract. Supplied broker preview data must be verified by real adapter. No orders are created or sent.",
    }
