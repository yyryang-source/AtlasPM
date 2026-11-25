"""Single-account, multi-asset *proposal* planner. Never places orders.

Supports existing long stocks, existing UST positions, new long protective puts,
and repayment of existing margin debt. Orders are hypothetical and immutable.
All brokerage previews in JSON are untrusted simulation inputs.
"""

from __future__ import annotations

from atlaspm.engine.contracts import validated_object

from atlaspm.engine.contracts import finite_number

from copy import deepcopy
from datetime import date

from typing import Any, Mapping

from atlaspm.execution.institutional_gate import assess_institutional_intent
from atlaspm.execution.institutional_ledger import build_institutional_ledger, _position


def _obj(value: Any, label: str) -> dict[str, Any]:
    return validated_object(value, label, invalid_message=f"{label} must be a JSON object")

def _num(value: Any, label: str, positive: bool = False) -> float:
    return finite_number(value, label, minimum=0, strict_minimum=positive,
    invalid_message=f"{label} must be a finite number",
    minimum_message=f"{label} must be positive" if positive else f"{label} must be nonnegative")

def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value.strip()


def _integer(value: Any, label: str) -> int:
    v = _num(value, label, positive=True)
    if int(v) != v:
        raise ValueError(f"{label} must be an integer")
    return int(v)


def propose_multi_asset_batch(
    ledger_payload: Mapping[str, Any], plan_payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Compute a marked hypothetical after-state and fail-closed RMS decisions.

    The plan's preview and snapshots are *not* broker-authenticated. A valid
    output is permission to inspect a paper proposal, never a fill.
    """
    ledger = build_institutional_ledger(ledger_payload)
    plan = _obj(plan_payload, "plan")
    account_id = _id(plan.get("account_id"), "account_id")
    batch_id = _id(plan.get("batch_id"), "batch_id")
    matching = [a for a in ledger["accounts"] if a["account_id"] == account_id]
    if len(matching) != 1:
        raise ValueError("batch.account_id must select exactly one ledger account")
    account = matching[0]
    raw_account = next(
        a for a in ledger_payload["accounts"] if a["account_id"] == account_id
    )
    source_positions = deepcopy(raw_account["positions"])
    source_by_key = {(p["type"], p["identifier"]): p for p in source_positions}
    marked_before = account["net_liquidation_calculated"]
    settled_cash = account["settled_cash"]
    receivable = account["unsettled_receivable"]
    payable = account["unsettled_payable"]
    debt = account["margin_debt"]
    initial_collateral_credit = account["broker_recognized_treasury_collateral_report_only"]
    policy = _obj(plan.get("policy"), "policy")
    broker = _obj(plan.get("broker_snapshot"), "broker_snapshot")
    market = _obj(plan.get("market"), "market")
    risk = _obj(plan.get("risk_observation"), "risk_observation")
    stress = _obj(plan.get("stress_assumptions"), "stress_assumptions")
    equity_shock = _num(stress.get("equity_decline_fraction"), "equity_decline_fraction")
    treasury_shock = _num(stress.get("treasury_decline_fraction"), "treasury_decline_fraction")
    if equity_shock > 1 or treasury_shock > 1:
        raise ValueError("Stress declines must be <=1")
    max_age = _num(policy.get("max_data_age_seconds"), "max_data_age_seconds", positive=True)
    max_deviation = _num(
        policy.get("max_limit_mark_deviation_bps"), "max_limit_mark_deviation_bps"
    )
    if not isinstance(plan.get("legs"), list) or not plan["legs"]:
        raise ValueError("legs must be a nonempty array")
    if len(plan["legs"]) > 20:
        raise ValueError("Maximum 20 legs per proposed basket")

    # All normalization occurs prior to evaluation; no user input is mutated.
    initial_keys = set(source_by_key)
    order_legs: list[dict[str, Any]] = []
    leg_results: list[dict[str, Any]] = []
    blockers: list[str] = []
    if not account["account_ledger_ready_for_risk_review"]:
        blockers.append("LEDGER_NOT_READY_FOR_RISK_REVIEW")
    if broker.get("account_id") != account_id:
        blockers.append("BROKER_SNAPSHOT_WRONG_ACCOUNT")
    if abs(_num(broker.get("net_liquidation"), "broker.net_liquidation") - marked_before) > ledger["reconciliation_tolerance_usd"]:
        blockers.append("BROKER_SNAPSHOT_NLV_MISMATCH")
    if abs(_num(broker.get("margin_debt"), "broker.margin_debt") - debt) > ledger["reconciliation_tolerance_usd"]:
        blockers.append("BROKER_SNAPSHOT_DEBT_MISMATCH")
    _num(broker.get("data_age_seconds"), "broker.data_age_seconds")
    _num(broker.get("available_funds"), "broker.available_funds")
    _num(broker.get("excess_liquidity"), "broker.excess_liquidity")
    if broker["data_age_seconds"] > max_age:
        blockers.append("BROKER_SNAPSHOT_STALE")

    fee_total = 0.0
    # Independent accounting delta: compare cash/debt/position valuation
    # against the expected fees and limit-vs-mark differences.
    expected_nlv_delta = 0.0
    newly_borrowed = 0.0
    # The full batch is account-specific. No separate account contributes
    # spendable cash or a collateral margin credit.
    for i, raw_leg in enumerate(plan["legs"]):
        leg = _obj(raw_leg, f"legs[{i}]")
        action = leg.get("action")
        if action not in {"BUY_EQUITY", "SELL_EQUITY", "BUY_TREASURY", "BUY_PUT", "REPAY_MARGIN"}:
            raise ValueError(f"Unsupported leg action: {action}")
        leg_id = _id(leg.get("leg_id"), f"legs[{i}].leg_id")
        if any(x["leg_id"] == leg_id for x in order_legs):
            raise ValueError("Duplicate leg_id")
        fee = _num(leg.get("fee", 0), f"{leg_id}.fee")
        if action == "REPAY_MARGIN" and fee != 0:
            raise ValueError("REPAY_MARGIN must have zero fee in this prototype")
        fee_total += fee
        notional = 0.0
        effect = ""
        if action in {"BUY_EQUITY", "SELL_EQUITY"}:
            identifier = _id(leg.get("identifier"), f"{leg_id}.identifier")
            key = ("equity", identifier)
            if key not in initial_keys:
                raise ValueError("Equity BUY/SELL is limited to existing long positions")
            pos = source_by_key[key]
            mark = _num(pos.get("mark_price"), f"{identifier}.mark_price", positive=True)
            if _num(pos.get("mark_age_seconds"), f"{identifier}.mark_age_seconds") > max_age:
                blockers.append("TRADED_EQUITY_MARK_STALE")
            qty = _integer(leg.get("shares"), f"{leg_id}.shares")
            limit = _num(leg.get("limit_price"), f"{leg_id}.limit_price", positive=True)
            if abs(limit / mark - 1) * 10000 > max_deviation + 1e-8:
                blockers.append("EQUITY_LIMIT_FAR_FROM_MARK")
            if action == "SELL_EQUITY" and qty > pos["shares"]:
                raise ValueError("Cannot sell more equity than held")
            pos["shares"] += qty if action == "BUY_EQUITY" else -qty
            notional = qty * limit
            if action == "SELL_EQUITY":
                # Sale settles later. Proceeds are NOT immediately spendable.
                receivable += notional - fee
                if fee > notional:
                    blockers.append("SELL_FEE_EXCEEDS_PROCEEDS")
                expected_nlv_delta += qty * (limit - mark) - fee
                effect = "UNSETTLED_RECEIVABLE_INCREASE"
            else:
                settled_cash -= notional + fee
                expected_nlv_delta += qty * (mark - limit) - fee
                effect = "SETTLED_CASH_OUTFLOW"
        elif action == "BUY_TREASURY":
            identifier = _id(leg.get("identifier"), f"{leg_id}.identifier")
            key = ("treasury", identifier)
            if key not in initial_keys:
                raise ValueError("Only existing, matched CUSIP Treasury positions can be enlarged")
            pos = source_by_key[key]
            dirty_mark = _num(pos["clean_price_per_100"], "clean_price_per_100", positive=True) + _num(pos["accrued_interest_per_100"], "accrued_interest_per_100")
            if _num(pos["mark_age_seconds"], "Treasury mark age") > max_age:
                blockers.append("TREASURY_MARK_STALE")
            face = _num(leg.get("face_value"), f"{leg_id}.face_value", positive=True)
            dirty_limit = _num(leg.get("dirty_price_per_100"), f"{leg_id}.dirty_price_per_100", positive=True)
            if abs(dirty_limit / dirty_mark - 1) * 10000 > max_deviation + 1e-8:
                blockers.append("TREASURY_DIRTY_LIMIT_FAR_FROM_MARK")
            notional = face * dirty_limit / 100
            settled_cash -= notional + fee
            pos["face_value"] += face
            expected_nlv_delta += face * (dirty_mark - dirty_limit) / 100 - fee
            effect = "SETTLED_CASH_OUTFLOW_NO_ADDITIONAL_COLLATERAL_CREDIT"
        elif action == "BUY_PUT":
            option_id = _id(leg.get("identifier"), f"{leg_id}.identifier")
            underlying = _id(leg.get("underlying"), f"{leg_id}.underlying")
            if ("option", option_id) in source_by_key:
                raise ValueError("This prototype buys only new protective put contracts")
            if ("equity", underlying) not in source_by_key:
                raise ValueError("Protective put requires underlying equity in this account")
            contracts = _integer(leg.get("contracts"), f"{leg_id}.contracts")
            multiplier = _integer(leg.get("multiplier"), f"{leg_id}.multiplier")
            strike = _num(leg.get("strike"), f"{leg_id}.strike", positive=True)
            expiry = _id(leg.get("expiration_date"), f"{leg_id}.expiration_date")
            try:
                expiry_day = date.fromisoformat(expiry)
            except ValueError as exc:
                raise ValueError("Invalid option expiry") from exc
            if expiry_day <= date.fromisoformat(ledger["valuation_at"][:10]):
                raise ValueError("Protective put expiration must be later than valuation date")
            mark_premium = _num(leg.get("mark_premium"), f"{leg_id}.mark_premium", positive=True)
            limit_premium = _num(leg.get("limit_premium"), f"{leg_id}.limit_premium", positive=True)
            age = _num(leg.get("quote_age_seconds"), f"{leg_id}.quote_age_seconds")
            if age > max_age:
                blockers.append("PUT_CHAIN_QUOTE_STALE")
            if abs(limit_premium / mark_premium - 1) * 10000 > max_deviation + 1e-8:
                blockers.append("PUT_LIMIT_FAR_FROM_MARK")
            permission = leg.get("trading_permission")
            if permission is not True:
                blockers.append("PROTECTIVE_PUT_PERMISSION_MISSING")
            pos = {
                "type": "option", "identifier": option_id, "underlying": underlying,
                "right": "put", "strike": strike, "expiration_date": expiry,
                "contracts": contracts, "multiplier": multiplier,
                "mark_premium": mark_premium,
                "mark_source": _id(leg.get("mark_source"), f"{leg_id}.mark_source"),
                "mark_age_seconds": age,
            }
            source_by_key[("option", option_id)] = pos
            notional = contracts * multiplier * limit_premium
            expected_nlv_delta += contracts * multiplier * (mark_premium - limit_premium) - fee
            settled_cash -= notional + fee
            effect = "LONG_PUT_PREMIUM_CASH_OUTFLOW_NO_ASSUMED_STRESS_CREDIT"
        else:
            if account["account_type"] != "margin":
                raise ValueError("Cannot repay margin debt from a cash account")
            amount = _num(leg.get("amount"), f"{leg_id}.amount", positive=True)
            if amount > debt + 1e-8:
                raise ValueError("Margin repayment exceeds outstanding principal")
            settled_cash -= amount
            debt -= amount
            notional = amount
            effect = "DECREASE_CASH_AND_MARGIN_PRINCIPAL"

        order_legs.append(dict(leg))
        leg_results.append({
            "leg_id": leg_id, "action": action, "notional": notional,
            "estimated_fee": fee, "accounting_effect": effect,
        })

    # Only the original brokerage cash account may fund buys/repayment.
    # Unsettled sale proceeds remain receivables until a settlement event.
    if settled_cash < payable - 1e-8:
        if account["account_type"] == "cash":
            blockers.append("CASH_ACCOUNT_INSUFFICIENT_SETTLED_FUNDS")
        else:
            opt_in = plan.get("allow_margin_borrowing", False)
            if not isinstance(opt_in, bool):
                raise ValueError("allow_margin_borrowing must be true or false")
            if not opt_in:
                blockers.append("NEW_FINANCING_NOT_EXPLICITLY_AUTHORIZED")
            else:
                newly_borrowed = payable - settled_cash
                debt += newly_borrowed
                settled_cash += newly_borrowed
    elif not isinstance(plan.get("allow_margin_borrowing", False), bool):
        raise ValueError("allow_margin_borrowing must be true or false")

    if settled_cash < 0 and account["account_type"] == "cash":
        # A blocked invalid cash-account proposal is reported but never
        # presented as a self-financing account after-state.
        blockers.append("CASH_ACCOUNT_NEGATIVE_SETTLED_BALANCE")
    if any(x.get("action") == "REPAY_MARGIN" for x in order_legs) and newly_borrowed > 0:
        blockers.append("CANNOT_REBORROW_TO_SIMULATE_MARGIN_REPAYMENT")

    # For a protective put, ensure enough *remaining* long underlying shares
    # to cover its total contract multiplier. Otherwise this is speculative
    # long put exposure, not a covered protection overlay.
    new_put_underlyings = {p["underlying"] for key, p in source_by_key.items()
                           if key[0] == "option" and key not in initial_keys}
    for underlying in sorted(new_put_underlyings):
        backing = source_by_key.get(("equity", underlying))
        required = sum(p["contracts"] * p["multiplier"] for key, p in source_by_key.items()
                       if key[0] == "option" and p["right"] == "put"
                       and p["underlying"] == underlying and p["contracts"] > 0)
        if backing is None or backing["shares"] + 1e-8 < required:
            blockers.append("PROTECTIVE_PUT_EXCEEDS_UNDERLYING_SHARES")

    projected: list[dict[str, Any]] = []
    for (kind, identifier), raw_pos in source_by_key.items():
        if kind == "equity" and raw_pos["shares"] <= 0:
            continue
        projected.append(_position(raw_pos, account_id))

    total_value_after = sum(row["market_value"] for row in projected)
    # Reflect options liabilities correctly as signed market values.
    proforma_nlv = (
        settled_cash + receivable - payable + total_value_after
        - debt - account["accrued_financing_interest"]
    )
    if proforma_nlv <= 0:
        blockers.append("NONPOSITIVE_PROFORMA_EQUITY")
    accounting_gap = (proforma_nlv - marked_before) - expected_nlv_delta
    ledger_balanced = abs(accounting_gap) <= max(0.01, abs(marked_before) * 1e-10)
    if not ledger_balanced:
        blockers.append("PRO_FORMA_LEDGER_DOES_NOT_BALANCE")
    gross_after = sum(row["gross_notional"] for row in projected)
    denominator = max(proforma_nlv, 1e-8)
    modeled_gross = gross_after / denominator
    # Never assume put protection in risk gate from intrinsic-only pricing.
    equity_value = sum(row["market_value"] for row in projected if row["type"] == "equity")
    ust_value = sum(row["market_value"] for row in projected if row["type"] == "treasury")
    conservative_loss = (
        equity_value * equity_shock + ust_value * treasury_shock
    ) / denominator
    reported_gross = _num(risk.get("pro_forma_gross_leverage"), "pro_forma_gross_leverage")
    reported_loss = _num(risk.get("pro_forma_stress_loss_fraction"), "pro_forma_stress_loss_fraction")
    if reported_gross + 1e-8 < modeled_gross:
        blockers.append("PROFORMA_GROSS_UNDERREPORTED")
    if reported_loss + 1e-8 < conservative_loss:
        blockers.append("PROFORMA_STRESS_UNDERREPORTED")

    preview = plan.get("basket_preview")
    if not isinstance(preview, dict):
        blockers.append("BASKET_WHATIF_PREVIEW_MISSING")
        preview = {}
    if (preview.get("batch_id") != batch_id
            or preview.get("account_id") != account_id
            or preview.get("order_legs") != order_legs):
        blockers.append("BASKET_PREVIEW_NOT_BOUND_TO_EXACT_LEGS")
    try:
        preview_debt = _num(preview.get("margin_debt_after"), "basket_preview.margin_debt_after")
        if abs(preview_debt - debt) > max(0.01, ledger["reconciliation_tolerance_usd"]):
            blockers.append("BASKET_PREVIEW_MARGIN_DEBT_MISMATCH")
        preview_nlv = _num(preview.get("net_liquidation_after"), "basket_preview.net_liquidation_after")
        if abs(preview_nlv - proforma_nlv) > max(0.01, ledger["reconciliation_tolerance_usd"]):
            blockers.append("BASKET_PREVIEW_NET_LIQUIDATION_MISMATCH")
    except ValueError:
        blockers.append("BASKET_PREVIEW_MALFORMED")

    gate_preview = {
        "accepted": preview.get("accepted"),
        "age_seconds": preview.get("age_seconds"),
        "available_funds_after": preview.get("available_funds_after"),
        "excess_liquidity_after": preview.get("excess_liquidity_after"),
        "margin_debt_after": preview.get("margin_debt_after"),
    }
    gate_account = {
        "type": account["account_type"], "net_liquidation": marked_before,
        "margin_debt": account["margin_debt"],
        "available_funds": broker["available_funds"],
        "excess_liquidity": broker["excess_liquidity"],
        "data_age_seconds": broker["data_age_seconds"],
        "reconciled": account["is_reconciled"],
    }
    gate_risk = {
        "current_drawdown_fraction": risk.get("current_drawdown_fraction"),
        "pro_forma_stress_loss_fraction": max(conservative_loss, reported_loss),
        "pro_forma_gross_leverage": max(modeled_gross, reported_gross),
    }
    gate_verdicts: list[dict[str, Any]] = []
    for row, original in zip(leg_results, order_legs):
        action = row["action"]
        converted = {
            "BUY_EQUITY": "ADD_EQUITY",
            "SELL_EQUITY": "REDUCE_EQUITY",
            "BUY_TREASURY": "BUY_TREASURY",
            "BUY_PUT": "BUY_PROTECTIVE_PUT",
            "REPAY_MARGIN": "REPAY_MARGIN",
        }[action]
        gate_payload = {
            "account": gate_account,
            "risk_observation": gate_risk,
            "intent": {
                "action": converted,
                "notional": row["notional"],
                "relies_on_treasury_collateral": False,
            },
            "policy": policy,
            "market": market,
            "broker_preview": gate_preview,
        }
        if action == "BUY_PUT":
            gate_payload["option_hedge"] = {
                "trading_permission": original["trading_permission"],
                "quote_age_seconds": original["quote_age_seconds"],
                "premium_and_fees": row["notional"] + row["estimated_fee"],
            }
        verdict = assess_institutional_intent(gate_payload)
        gate_verdicts.append({
            "leg_id": row["leg_id"], "action": action,
            "decision": verdict["decision"], "risk_state": verdict["risk_state"],
            "reasons": verdict["block_reasons"],
        })
        blockers.extend(verdict["block_reasons"])

    # A 1x cash account never gets borrowing permission by this abstraction.
    if account["account_type"] == "cash" and debt > 0:
        blockers.append("CASH_ACCOUNT_FINANCING_FORBIDDEN")

    # The same collateral may back an existing broker loan, but it must NOT
    # be counted as additional buying power when new Treasury face is added.
    original_credit = initial_collateral_credit
    projected_credit = sum(
        row.get("broker_recognized_collateral_value", 0.0)
        for row in projected if row["type"] == "treasury"
    )
    if abs(projected_credit - original_credit) > 1e-8:
        blockers.append("UNEXPECTED_COLLATERAL_CREDIT_CHANGE")

    decision = "ALLOW_PAPER_INTENT" if not blockers else "BLOCK"
    return {
        "method": "read_only_multi_asset_batch_proforma_and_rms",
        "batch_id": batch_id,
        "account_id": account_id,
        "before": {
            "net_liquidation": marked_before,
            "settled_cash": account["settled_cash"],
            "unsettled_receivable": account["unsettled_receivable"],
            "margin_debt": account["margin_debt"],
            "gross_marked_notional": account["gross_marked_notional"],
        },
        "pro_forma_unexecuted": {
            "net_liquidation": proforma_nlv,
            "settled_cash": settled_cash,
            "unsettled_receivable": receivable,
            "unsettled_payable": payable,
            "margin_debt": debt,
            "new_margin_borrowing": newly_borrowed,
            "gross_marked_notional": gross_after,
            "modeled_gross_to_nlv": modeled_gross,
            "conservative_stress_loss_fraction_without_put_credit": conservative_loss,
            "broker_recognized_treasury_credit_report_only": projected_credit,
            "total_fee": fee_total,
            "expected_net_liquidation_change_from_fees_and_price_deviation": expected_nlv_delta,
            "net_liquidation_change_reconciliation_gap": accounting_gap,
            "positions": projected,
            "self_financing_identity_verified": ledger_balanced,
        },
        "legs": leg_results,
        "leg_risk_verdicts": gate_verdicts,
        "decision": decision,
        "block_reasons": sorted(set(blockers)),
        "live_execution_enabled": False,
        "broker_preview_is_authenticated": False,
        "warnings": [
            "Research-only basket proposal; nothing submitted, purchased, settled, assigned, or exercised.",
            "Input broker snapshots and What-If preview are unverified synthetic contracts; no live broker interface.",
            "Equity sales create unsettled receivables which do not finance purchases until settlement.",
            "New Treasury face is NOT assumed to generate any extra usable collateral credit.",
            "Purchased put valuation is a mark only; NO put protection credit is used to pass portfolio stress limits.",
            "Real broker may not offer a single combined What-If preview for all asset classes; each leg needs broker verification.",
        ],
    }
