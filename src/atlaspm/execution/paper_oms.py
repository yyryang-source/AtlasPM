"""Deterministic, read-only paper OMS replay with fill accounting.

Consumes a synthetically approved multi-asset basket and a local event journal.
It NEVER sends orders or mutates user brokerage records. All broker previews
are unverified JSON and cannot authorize live execution.
"""

from __future__ import annotations

from atlaspm.engine.contracts import finite_number

from copy import deepcopy
from datetime import datetime

from typing import Any, Mapping

from atlaspm.execution.institutional_batch import propose_multi_asset_batch
from atlaspm.execution.institutional_ledger import build_institutional_ledger, _position, _timestamp
from atlaspm.execution.oms_controls import chain_audit_events, post_fill_risk_snapshot, verify_audit_chain


def _map(x: object, name: str) -> dict[str, Any]:
    if not isinstance(x, dict):
        raise ValueError(f"{name} must be an object")
    return x


def _text(x: object, name: str) -> str:
    if not isinstance(x, str) or not x.strip():
        raise ValueError(f"{name} must be nonempty text")
    return x.strip()


def _number(x: object, name: str, *, positive: bool = False) -> float:
    return finite_number(x, name, minimum=0, strict_minimum=positive,
    invalid_message=f"{name} must be finite and numeric",
    minimum_message=f"{name} must be positive" if positive else f"{name} must be nonnegative")

def _order_quantity(leg: dict[str, Any]) -> float:
    action = leg["action"]
    field = {
        "BUY_EQUITY": "shares", "SELL_EQUITY": "shares",
        "BUY_TREASURY": "face_value", "BUY_PUT": "contracts",
        "REPAY_MARGIN": "amount",
    }[action]
    return _number(leg.get(field), field, positive=True)


def replay_paper_orders(
    ledger_payload: Mapping[str, Any],
    plan_payload: Mapping[str, Any],
    journal_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Replay paper-only order events, preserving cash/debt/position invariants."""
    # Re-run every central risk gate BEFORE permitting any synthetic paper fill.
    approval = propose_multi_asset_batch(ledger_payload, plan_payload)
    if approval["decision"] != "ALLOW_PAPER_INTENT":
        raise ValueError("Batch risk preflight is blocked; paper OMS cannot start")
    ledger = build_institutional_ledger(ledger_payload)
    plan = _map(plan_payload, "plan")
    journal = _map(journal_payload, "journal")
    if journal.get("batch_id") != approval["batch_id"]:
        raise ValueError("Journal must match approved batch_id")
    if journal.get("account_id") != approval["account_id"]:
        raise ValueError("Journal must match approved account_id")
    raw_events = journal.get("events")
    if not isinstance(raw_events, list) or not raw_events or len(raw_events) > 10000:
        raise ValueError("events must contain 1..10000 journal entries")
    account_id = approval["account_id"]
    account = next(x for x in ledger["accounts"] if x["account_id"] == account_id)
    raw_account = next(x for x in ledger_payload["accounts"] if x["account_id"] == account_id)
    positions = {
        (p["type"], p["identifier"]): deepcopy(p)
        for p in raw_account["positions"]
    }
    beginning_equity = float(account["net_liquidation_calculated"])
    cash = float(account["settled_cash"])
    receivable = float(account["unsettled_receivable"])
    payable = float(account["unsettled_payable"])
    debt = float(account["margin_debt"])
    financing_accrual = float(account["accrued_financing_interest"])
    expected_equity_change = 0.0
    orders: dict[str, dict[str, Any]] = {}
    for leg in plan["legs"]:
        key = _text(leg["leg_id"], "leg_id")
        if key in orders:
            raise ValueError("Duplicate leg_id in approved plan")
        orders[key] = {
            "leg_id": key, "action": leg["action"],
            "requested_quantity": _order_quantity(leg),
            "filled_quantity": 0.0, "filled_notional": 0.0,
            "fees_paid": 0.0, "status": "NEW",
        }
    seen_events: dict[str, dict[str, Any]] = {}
    seen_exec: dict[str, dict[str, Any]] = {}
    fill_records: dict[str, dict[str, Any]] = {}
    settled_executions: set[str] = set()
    audit: list[dict[str, Any]] = []
    post_fill_risk_snapshots: list[dict[str, Any]] = []
    post_fill_breaches: list[str] = []
    sequence = 0
    last_time: datetime | None = None
    halted = False
    halt_reason: str | None = None
    duplicate_count = 0

    def marked_equity() -> tuple[float, list[dict[str, Any]]]:
        valued = [
            _position(raw, account_id) for raw in positions.values()
            if raw["type"] != "equity" or raw["shares"] > 0
        ]
        marked = (
            sum(p["market_value"] for p in valued) + cash + receivable
            - payable - debt - financing_accrual
        )
        return marked, valued

    for raw_event in raw_events:
        e = _map(raw_event, "event")
        event_id = _text(e.get("event_id"), "event_id")
        if event_id in seen_events:
            if e != seen_events[event_id]:
                raise ValueError("Conflicting duplicate event_id")
            duplicate_count += 1
            continue
        if halted:
            raise ValueError(f"{halt_reason}: freeze event replay until reconciliation")
        seq = e.get("sequence")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq <= sequence:
            raise ValueError("Unique journal sequences must be strictly increasing integers")
        timestamp = _timestamp(e.get("timestamp"), "event.timestamp")
        if last_time is not None and timestamp < last_time:
            raise ValueError("Journal timestamps must not move backward")
        sequence, last_time = seq, timestamp
        event_type = e.get("type")
        leg_id = _text(e.get("leg_id"), "event.leg_id")
        if leg_id not in orders:
            raise ValueError("Event must reference an existing approved leg_id")
        state = orders[leg_id]
        leg = next(x for x in plan["legs"] if x["leg_id"] == leg_id)
        prior_state = state["status"]

        if event_type == "SUBMIT":
            if prior_state != "NEW":
                raise ValueError("Only NEW paper orders can be submitted")
            state["status"] = "SUBMITTED"
        elif event_type == "ACK":
            if prior_state != "SUBMITTED":
                raise ValueError("ACK requires a submitted paper order")
            state["status"] = "ACCEPTED"
        elif event_type == "CANCEL_REQUEST":
            if prior_state not in {"SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED"}:
                raise ValueError("Cancel request requires an open order")
            state["status"] = "CANCEL_REQUESTED"
        elif event_type == "CANCELED":
            if prior_state not in {"SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "CANCEL_REQUESTED"}:
                raise ValueError("CANCELED requires an open/cancel-pending order")
            state["status"] = "CANCELED"
        elif event_type == "REJECT":
            if prior_state not in {"SUBMITTED", "ACCEPTED"}:
                raise ValueError("REJECT requires a nonfilled submitted/accepted order")
            state["status"] = "REJECTED"
        elif event_type == "UNKNOWN_OUTCOME":
            if prior_state in {"NEW", "FILLED", "CANCELED", "REJECTED", "UNKNOWN"}:
                raise ValueError("Unknown outcome requires a nonterminal submitted order")
            state["status"] = "UNKNOWN"
            halted = True
            halt_reason = "Unknown order outcome"
        elif event_type == "FILL":
            execution_id = _text(e.get("execution_id"), "execution_id")
            if execution_id in seen_exec:
                if e["leg_id"] != seen_exec[execution_id]["leg_id"] or any(
                    e.get(k) != seen_exec[execution_id].get(k) for k in ("quantity", "price", "fee")
                ):
                    raise ValueError("Conflicting duplicate execution_id")
                # A repeated execution can be delivered with a different
                # journal event_id; neither positions nor cash are changed.
                duplicate_count += 1
                seen_events[event_id] = dict(e)
                continue
            if prior_state not in {"SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "CANCEL_REQUESTED"}:
                raise ValueError("Cannot apply a fill to a non-open paper order")
            quantity = _number(e.get("quantity"), "fill.quantity", positive=True)
            if quantity > state["requested_quantity"] - state["filled_quantity"] + 1e-8:
                raise ValueError("Fill quantity exceeds remaining approved order")
            if state["action"] in {"BUY_EQUITY", "SELL_EQUITY", "BUY_PUT"} and quantity != int(quantity):
                raise ValueError("Shares/contracts must fill in whole units")
            fee = _number(e.get("fee", 0), "fill.fee")
            action = state["action"]
            if action == "REPAY_MARGIN":
                if "price" in e or fee != 0:
                    raise ValueError("Margin repayment fill has no price or fee")
                if account["account_type"] != "margin" or debt + 1e-8 < quantity:
                    raise ValueError("Cannot repay more than existing margin debt")
                if cash - payable + 1e-8 < quantity:
                    raise ValueError("Margin debt repayment requires settled unencumbered cash")
                cash -= quantity
                debt -= quantity
                notional, delta = quantity, 0.0
            else:
                price = _number(e.get("price"), "fill.price", positive=True)
                if action in {"BUY_EQUITY", "SELL_EQUITY"}:
                    limit = _number(leg["limit_price"], "limit_price", positive=True)
                    mark = _number(positions["equity", leg["identifier"]]["mark_price"], "mark", positive=True)
                    if action == "BUY_EQUITY" and price > limit + 1e-9:
                        raise ValueError("Buy fill price worse than approved limit")
                    if action == "SELL_EQUITY" and price + 1e-9 < limit:
                        raise ValueError("Sell fill price worse than approved limit")
                    notional = quantity * price
                    pos = positions["equity", leg["identifier"]]
                    if action == "BUY_EQUITY":
                        pos["shares"] += quantity
                        delta = quantity * (mark - price) - fee
                    else:
                        if pos["shares"] + 1e-8 < quantity:
                            raise ValueError("Cannot sell more shares than held")
                        if fee > notional:
                            raise ValueError("Sale fee exceeds proceeds")
                        pos["shares"] -= quantity
                        receivable += notional - fee
                        delta = quantity * (price - mark) - fee
                elif action == "BUY_TREASURY":
                    limit = _number(leg["dirty_price_per_100"], "dirty_price_per_100", positive=True)
                    if price > limit + 1e-9:
                        raise ValueError("Treasury fill price worse than approved dirty limit")
                    pos = positions["treasury", leg["identifier"]]
                    dirty_mark = pos["clean_price_per_100"] + pos["accrued_interest_per_100"]
                    notional = quantity * price / 100.0
                    pos["face_value"] += quantity
                    delta = quantity * (dirty_mark - price) / 100.0 - fee
                    # The original broker collateral credit stays unchanged.
                elif action == "BUY_PUT":
                    limit = _number(leg["limit_premium"], "limit_premium", positive=True)
                    if price > limit + 1e-9:
                        raise ValueError("Put fill premium worse than approved limit")
                    option_key = ("option", leg["identifier"])
                    if option_key not in positions:
                        positions[option_key] = {
                            "type": "option", "identifier": leg["identifier"],
                            "underlying": leg["underlying"], "right": "put",
                            "strike": leg["strike"], "expiration_date": leg["expiration_date"],
                            "contracts": 0, "multiplier": leg["multiplier"],
                            "mark_premium": leg["mark_premium"],
                            "mark_source": leg["mark_source"],
                            "mark_age_seconds": leg["quote_age_seconds"],
                        }
                    pos = positions[option_key]
                    pos["contracts"] += quantity
                    notional = quantity * leg["multiplier"] * price
                    delta = quantity * leg["multiplier"] * (pos["mark_premium"] - price) - fee
                else:
                    raise ValueError("Unsupported paper OMS fill action")
                if action != "SELL_EQUITY":
                    spending = notional + fee
                    free = max(cash - payable, 0.0)
                    spend_cash = min(spending, free)
                    cash -= spend_cash
                    remainder = spending - spend_cash
                    if remainder > 1e-8:
                        if (
                            account["account_type"] != "margin"
                            or plan.get("allow_margin_borrowing", False) is not True
                            or action == "BUY_PUT"
                        ):
                            raise ValueError("Unsettled proceeds or unauthorized margin cannot fund paper fill")
                        debt += remainder
            expected_equity_change += delta
            state["filled_quantity"] += quantity
            state["filled_notional"] += notional
            state["fees_paid"] += fee
            state["status"] = (
                "FILLED" if abs(state["requested_quantity"] - state["filled_quantity"]) <= 1e-8
                else "PARTIALLY_FILLED"
            )
            seen_exec[execution_id] = dict(e)
            fill_records[execution_id] = {
                "leg_id": leg_id, "action": action,
                "quantity": quantity, "price": e.get("price"), "fee": fee,
                "timestamp": timestamp.isoformat(),
                "settled": False if action == "SELL_EQUITY" else None,
                "net_sale_receivable": notional - fee if action == "SELL_EQUITY" else None,
            }
        elif event_type == "SETTLE_SALE":
            exec_id = _text(e.get("execution_id"), "settle.execution_id")
            if exec_id not in fill_records:
                raise ValueError("Cannot settle unknown execution")
            fill = fill_records[exec_id]
            if fill["action"] != "SELL_EQUITY" or fill["leg_id"] != leg_id:
                raise ValueError("SETTLE_SALE must identify matching sell execution")
            if exec_id in settled_executions:
                raise ValueError("Sale execution cannot settle twice")
            if timestamp.date() <= datetime.fromisoformat(fill["timestamp"]).date():
                raise ValueError("Sale cannot settle on or before its execution date")
            amount = float(fill["net_sale_receivable"])
            if receivable + 1e-8 < amount:
                raise ValueError("Insufficient unsettled receivable for sale settlement")
            receivable -= amount
            cash += amount
            fill["settled"] = True
            settled_executions.add(exec_id)
        else:
            raise ValueError(f"Unsupported paper OMS event type: {event_type}")

        marked, marked_positions = marked_equity()
        gap = (marked - beginning_equity) - expected_equity_change
        if abs(gap) > max(0.01, beginning_equity * 1e-10):
            raise ValueError("Paper ledger failed independent cash/debt/mark reconciliation")
        if event_type == "FILL":
            after_fill = post_fill_risk_snapshot(
                marked_positions,
                marked_net_liquidation=marked,
                settled_cash=cash,
                unsettled_payable=payable,
                margin_debt=debt,
                account_type=account["account_type"],
                max_gross_leverage=plan["policy"]["max_gross_leverage"],
                max_stress_loss_fraction=plan["policy"]["max_stress_loss_fraction"],
                equity_decline_fraction=plan["stress_assumptions"]["equity_decline_fraction"],
                treasury_decline_fraction=plan["stress_assumptions"]["treasury_decline_fraction"],
            )
            post_fill_risk_snapshots.append({
                "event_id": event_id, "leg_id": leg_id,
                **after_fill,
            })
            if after_fill["breaches"]:
                post_fill_breaches.extend(after_fill["breaches"])
                halted = True
                halt_reason = "Post-fill risk budget breached"
        seen_events[event_id] = dict(e)
        audit.append({
            "event_id": event_id, "sequence": seq,
            "timestamp": timestamp.isoformat(), "leg_id": leg_id,
            "type": event_type, "before_state": prior_state,
            "after_state": state["status"], "marked_net_liquidation": marked,
            "reconciliation_gap": gap,
            "source_event": dict(e),
            "post_fill_risk_state": (
                post_fill_risk_snapshots[-1]["post_fill_risk_state"]
                if event_type == "FILL" else None
            ),
        })
    ending_nlv, marked_positions = marked_equity()
    audit, audit_root = chain_audit_events(audit)
    if not verify_audit_chain(audit, audit_root):
        raise ValueError("Audit hash chain failed its self-consistency check")
    unresolved = [x["leg_id"] for x in orders.values() if x["status"] == "UNKNOWN"]
    total_credit = sum(
        x.get("broker_recognized_collateral_value", 0.0)
        for x in marked_positions if x["type"] == "treasury"
    )
    if abs(total_credit - account["broker_recognized_treasury_collateral_report_only"]) > 1e-8:
        raise ValueError("Paper OMS attempted to invent additional Treasury credit")
    return {
        "method": "offline_event_replayed_paper_oms",
        "batch_id": approval["batch_id"], "account_id": account_id,
        "decision": ("HALT_RECONCILIATION" if unresolved else
                     "HALT_RISK" if post_fill_breaches else "PAPER_REPLAY_COMPLETED"),
        "live_execution_enabled": False,
        "broker_preview_is_authenticated": False,
        "approved_synthetic_preflight": True,
        "orders": list(orders.values()),
        "audit_events": audit,
        "audit_root_sha256": audit_root,
        "audit_hash_chain_self_consistent": True,
        "post_fill_risk_snapshots": post_fill_risk_snapshots,
        "post_fill_risk_breaches": sorted(set(post_fill_breaches)),
        "broker_margin_rechecked_after_fills": False,
        "duplicate_events_ignored": duplicate_count,
        "unresolved_orders": unresolved,
        "fill_records": fill_records,
        "account_replay": {
            "opening_net_liquidation": beginning_equity,
            "marked_net_liquidation": ending_nlv,
            "expected_net_liquidation_change_from_fills_and_fees": expected_equity_change,
            "net_liquidation_reconciliation_gap": (
                (ending_nlv - beginning_equity) - expected_equity_change
            ),
            "settled_cash": cash, "unsettled_receivable": receivable,
            "unsettled_payable": payable, "margin_debt": debt,
            "accrued_financing_interest": financing_accrual,
            "broker_recognized_treasury_credit_report_only": total_credit,
            "positions": marked_positions,
        },
        "warnings": [
            "Offline paper replay only; events, fills, broker preview and quote marks are synthetic/unverified.",
            "NO orders were sent, and no external account or original JSON file was modified.",
            "Sale proceeds settle only via explicitly replayed SETTLE_SALE events and cannot pay earlier buys.",
            "A broker may fill orders during cancellation or after connectivity loss; UNKNOWN_OUTCOME halts replay.",
            "No real-time broker buying power, intraday margin, exchange calendar or actual execution safety is modeled.",
            "Each fill is checked against static gross and stress budgets; a breach halts subsequent replay events.",
            "The SHA-256 chain detects local modification of a saved audit but is unsigned; it does NOT authenticate broker origin.",
        ],
    }
