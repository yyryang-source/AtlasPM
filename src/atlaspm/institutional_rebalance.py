"""Read-only equity add/reduce planner using the institutional ledger and RMS.

No broker connectivity, no actual orders, no order-state persistence, and no
authority to execute. Fake broker previews in tests are for contract testing.
"""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any, Mapping

from atlaspm.institutional_gate import assess_institutional_intent
from atlaspm.institutional_ledger import build_institutional_ledger


def _obj(x: object, name: str) -> dict[str, Any]:
    if not isinstance(x, dict):
        raise ValueError(f"{name} must be a JSON object")
    return x


def _num(x: object, name: str, *, allow_zero: bool = True) -> float:
    if isinstance(x, bool) or not isinstance(x, (float, int)) or not isfinite(x):
        raise ValueError(f"{name} must be a finite number")
    value = float(x)
    if value < 0 or (not allow_zero and value == 0):
        raise ValueError(f"{name} must be nonnegative" if allow_zero else f"{name} must be positive")
    return value


def _text(x: object, name: str) -> str:
    if not isinstance(x, str) or not x.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return x.strip()


def plan_equity_trade(
    ledger_payload: Mapping[str, Any], plan_payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Calculate a pro-forma equity add/reduce and obtain paper-only RMS decision.

    Only already-held long cash equities are supported by this prototype.
    Every provided broker-preview field is treated as **unverified input**.
    """
    ledger = build_institutional_ledger(ledger_payload)
    p = _obj(plan_payload, "trade_plan")
    account_id = _text(p.get("account_id"), "account_id")
    candidates = [a for a in ledger["accounts"] if a["account_id"] == account_id]
    if len(candidates) != 1:
        raise ValueError("Trade plan account_id must select exactly one ledger account")
    account = candidates[0]
    raw_accounts = _obj(ledger_payload, "ledger_payload")["accounts"]
    raw_account = next(a for a in raw_accounts if a["account_id"] == account_id)
    trade = _obj(p.get("trade"), "trade")
    side = trade.get("side")
    if side not in {"BUY", "SELL"}:
        raise ValueError("trade.side must be BUY or SELL")
    symbol = _text(trade.get("symbol"), "trade.symbol")
    shares = _num(trade.get("shares"), "trade.shares", allow_zero=False)
    if int(shares) != shares:
        raise ValueError("trade.shares must be a positive whole-share number")
    limit_price = _num(trade.get("limit_price"), "trade.limit_price", allow_zero=False)
    estimated_fee = _num(trade.get("estimated_fee"), "trade.estimated_fee")
    client_order_id = _text(trade.get("client_order_id"), "trade.client_order_id")
    if len(client_order_id) > 128:
        raise ValueError("client_order_id too long")
    may_borrow = trade.get("allow_margin_borrowing", False)
    if not isinstance(may_borrow, bool):
        raise ValueError("allow_margin_borrowing must be boolean")
    positions = [
        pos for pos in account["positions"]
        if pos["type"] == "equity" and pos["identifier"] == symbol
    ]
    if len(positions) != 1:
        raise ValueError(
            "This prototype only plans BUY/SELL for an already-held long equity"
        )
    equity_position = positions[0]
    if side == "SELL" and shares > equity_position["shares"] + 1e-9:
        raise ValueError("SELL quantity exceeds existing long position")

    policy = _obj(p.get("policy"), "policy")
    broker = _obj(p.get("broker_snapshot"), "broker_snapshot")
    market = _obj(p.get("market"), "market")
    risk = _obj(p.get("risk_observation"), "risk_observation")
    preview = p.get("broker_preview")
    preview_obj = preview if isinstance(preview, dict) else None
    blockers: list[str] = []
    warnings: list[str] = []

    nlv = float(account["net_liquidation_calculated"])
    pre_cash = float(account["settled_cash"])
    pre_debt = float(account["margin_debt"])
    market_price = float(equity_position["mark_price"])
    gross_trade_value = shares * limit_price
    price_gap_bps = abs(limit_price / market_price - 1) * 10000 if market_price > 0 else float("inf")
    max_gap_bps = _num(
        policy.get("max_limit_mark_deviation_bps"), "max_limit_mark_deviation_bps"
    )
    if price_gap_bps > max_gap_bps + 1e-8:
        blockers.append("LIMIT_PRICE_TOO_FAR_FROM_ACCOUNT_MARK")
    if equity_position["mark_age_seconds"] > _num(policy.get("max_data_age_seconds"), "max_data_age_seconds"):
        blockers.append("TRADED_INSTRUMENT_MARK_STALE")
    if not account["account_ledger_ready_for_risk_review"]:
        blockers.append("LEDGER_NOT_READY_FOR_RISK_REVIEW")

    # Reconciliation with the *independently provided* broker snapshot is
    # necessary but not sufficient: all values are still unverified locally.
    broker_nlv = _num(broker.get("net_liquidation"), "broker_snapshot.net_liquidation", allow_zero=False)
    broker_debt = _num(broker.get("margin_debt"), "broker_snapshot.margin_debt")
    tolerance = float(ledger["reconciliation_tolerance_usd"])
    if abs(broker_nlv - nlv) > tolerance:
        blockers.append("BROKER_SNAPSHOT_NLV_MISMATCH")
    if abs(broker_debt - pre_debt) > tolerance:
        blockers.append("BROKER_SNAPSHOT_DEBT_MISMATCH")
    if broker.get("account_id") != account_id:
        blockers.append("BROKER_SNAPSHOT_WRONG_ACCOUNT")

    # Selling adds net simulated cash. Buying uses settled cash and may borrow
    # only if margin account + explicit opt-in. Debt is never auto-repaid on a
    # sale: a future separate payment event must reconcile that cash movement.
    if side == "BUY":
        cost = gross_trade_value + estimated_fee
        cash_used = min(max(pre_cash, 0.0), cost)
        borrowing = cost - cash_used
        post_cash = pre_cash - cash_used
        post_debt = pre_debt + borrowing
        if borrowing > 1e-9:
            if account["account_type"] == "cash":
                blockers.append("CASH_ACCOUNT_INSUFFICIENT_SETTLED_CASH")
            elif not may_borrow:
                blockers.append("MARGIN_BORROWING_NOT_EXPLICITLY_AUTHORIZED")
    else:
        borrowing = 0.0
        post_cash = pre_cash + gross_trade_value - estimated_fee
        post_debt = pre_debt
    if post_cash < -1e-8:
        blockers.append("NEGATIVE_SETTLED_CASH")
    if estimated_fee > gross_trade_value and side == "SELL":
        blockers.append("SELL_ESTIMATED_FEE_EXCEEDS_PROCEEDS")

    change_in_shares = shares if side == "BUY" else -shares
    pro_forma_shares = equity_position["shares"] + change_in_shares
    # At the unchanged account mark, price/fee slippage is properly
    # represented by the difference from limit-price cash movement.
    position_mark_after = pro_forma_shares * market_price
    pro_forma_nlv = nlv + (post_cash - pre_cash) - (post_debt - pre_debt) + (
        position_mark_after - equity_position["market_value"]
    )
    if pro_forma_nlv <= 0:
        blockers.append("NONPOSITIVE_PROFORMA_EQUITY")
    gross_marked_value_after = account["gross_marked_notional"] + change_in_shares * market_price
    if gross_marked_value_after < 0:
        raise ValueError("Impossible negative pro forma gross notional")

    # Conservative shock estimate used as a lower bound for the RMS
    # observation. Options are not credited with protective P&L without
    # independently verified scenario pricing.
    stress = _obj(p.get("stress_assumptions"), "stress_assumptions")
    equity_decline = _num(stress.get("equity_decline_fraction"), "equity_decline_fraction")
    treasury_decline = _num(stress.get("treasury_decline_fraction"), "treasury_decline_fraction")
    if equity_decline > 1 or treasury_decline > 1:
        raise ValueError("Stress declines must lie in [0, 1]")
    account_equity_value = sum(
        pos["market_value"] for pos in account["positions"] if pos["type"] == "equity"
    )
    account_treasury_value = sum(
        pos["market_value"] for pos in account["positions"] if pos["type"] == "treasury"
    )
    equity_value_after = account_equity_value + change_in_shares * market_price
    min_stress_loss = (
        equity_value_after * equity_decline +
        account_treasury_value * treasury_decline
    ) / max(pro_forma_nlv, 1e-8)
    min_gross = gross_marked_value_after / max(pro_forma_nlv, 1e-8)
    supplied_gross = _num(risk.get("pro_forma_gross_leverage"), "pro_forma_gross_leverage")
    supplied_stress = _num(risk.get("pro_forma_stress_loss_fraction"), "pro_forma_stress_loss_fraction")
    if supplied_gross + 1e-8 < min_gross:
        blockers.append("PROFORMA_LEVERAGE_UNDERREPORTED")
    if supplied_stress + 1e-8 < min_stress_loss:
        blockers.append("PROFORMA_STRESS_UNDERREPORTED")

    # Require the broker What-If preview to refer to the EXACT proposed
    # client, account, side, symbol, quantity and price. Generic preview
    # results must never be reused for a different proposal.
    if preview_obj is not None:
        if (preview_obj.get("client_order_id") != client_order_id
                or preview_obj.get("account_id") != account_id
                or preview_obj.get("symbol") != symbol
                or preview_obj.get("side") != side):
            blockers.append("BROKER_PREVIEW_NOT_BOUND_TO_TRADE")
        try:
            preview_shares = _num(preview_obj.get("shares"), "broker_preview.shares")
            preview_price = _num(preview_obj.get("limit_price"), "broker_preview.limit_price")
            if abs(preview_shares - shares) > 1e-8 or abs(preview_price - limit_price) > 1e-8:
                blockers.append("BROKER_PREVIEW_NOT_BOUND_TO_TRADE")
            debt_after_preview = _num(
                preview_obj.get("margin_debt_after"), "broker_preview.margin_debt_after"
            )
            if abs(debt_after_preview - post_debt) > max(0.01, tolerance):
                blockers.append("BROKER_PREVIEW_DEBT_MISMATCH")
        except ValueError:
            blockers.append("BROKER_PREVIEW_MALFORMED_OR_MISSING_FIELDS")

    preflight = {
        "account": {
            "type": account["account_type"],
            "net_liquidation": nlv,
            "margin_debt": pre_debt,
            "available_funds": broker.get("available_funds"),
            "excess_liquidity": broker.get("excess_liquidity"),
            "data_age_seconds": broker.get("data_age_seconds"),
            "reconciled": bool(account["is_reconciled"]),
        },
        "intent": {
            "action": "ADD_EQUITY" if side == "BUY" else "REDUCE_EQUITY",
            "notional": gross_trade_value,
            "relies_on_treasury_collateral": False,
        },
        "risk_observation": {
            "current_drawdown_fraction": risk.get("current_drawdown_fraction"),
            "pro_forma_stress_loss_fraction": max(supplied_stress, min_stress_loss),
            "pro_forma_gross_leverage": max(supplied_gross, min_gross),
        },
        "policy": policy,
        "market": market,
        "broker_preview": preview_obj,
    }
    verdict = assess_institutional_intent(preflight)
    blockers.extend(verdict["block_reasons"])
    warnings.extend(verdict["warnings"])
    decision = (
        "ALLOW_PAPER_INTENT" if not blockers and verdict["eligible_for_paper_intent"]
        else "BLOCK"
    )
    return {
        "method": "read_only_equity_order_planner_and_risk_preflight",
        "client_order_id": client_order_id,
        "account_id": account_id,
        "action": "ADD_EQUITY" if side == "BUY" else "REDUCE_EQUITY",
        "symbol": symbol,
        "side": side,
        "shares": int(shares),
        "limit_price": limit_price,
        "trade_notional": gross_trade_value,
        "estimated_fee": estimated_fee,
        "price_deviation_from_mark_bps": price_gap_bps,
        "before": {
            "settled_cash": pre_cash, "margin_debt": pre_debt,
            "shares": equity_position["shares"], "net_liquidation": nlv,
        },
        "pro_forma_after_unexecuted_trade": {
            "settled_cash": post_cash,
            "margin_debt": post_debt,
            "incremental_margin_borrowing": borrowing,
            "shares": pro_forma_shares,
            "market_value_at_original_mark": position_mark_after,
            "net_liquidation_at_original_marks": pro_forma_nlv,
            "conservative_gross_marked_notional_to_nlv": min_gross,
            "conservative_uniform_stress_loss_fraction": min_stress_loss,
        },
        "rms": verdict,
        "decision": decision,
        "eligible_for_paper_intent": decision == "ALLOW_PAPER_INTENT",
        "live_execution_enabled": False,
        "block_reasons": sorted(set(blockers)),
        "warnings": sorted(set(warnings + [
            "No trade created, placed or simulated as a broker fill.",
            "Broker snapshots, preview data, quotes, and stress inputs are external/unverified in this local research version.",
            "The cash/debt projection is illustrative; real margin accounting, settlement and buying power require broker reconciliation.",
        ])),
    }
