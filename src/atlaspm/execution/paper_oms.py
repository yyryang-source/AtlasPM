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




_LIFECYCLE_TYPES={"OPTION_EXPIRY","OPTION_DELIVERY","TREASURY_LIFECYCLE"}


def _load_lifecycle_history(journal, kind):
    """Load the existing normalized history under local_data, bound to file hashes."""
    from hashlib import sha256
    from pathlib import Path
    from atlaspm.data.market_db import private_path
    specs=journal.get("lifecycle_sources",{}).get(kind)
    names=("contracts","quotes","settlements") if kind=="option" else ("contracts","payments","quotes")
    if not isinstance(specs,dict) or set(specs)!=set(names):
        raise ValueError("OMS_LIFECYCLE_HISTORY_REQUIRED")
    paths=[]
    hashes=[]
    for name in names:
        spec=specs[name]
        if not isinstance(spec,dict) or set(spec)!={"path","sha256"}:
            raise ValueError("OMS_LIFECYCLE_SOURCE_CONTRACT")
        path=private_path(Path(spec["path"]),exists=True)
        if path.stat().st_size>8*1024*1024:
            raise ValueError("OMS_LIFECYCLE_SOURCE_BUDGET")
        with path.open("rb") as handle:
            body=handle.read(8*1024*1024+1)
        if len(body)>8*1024*1024 or sha256(body).hexdigest()!=spec["sha256"]:
            raise ValueError("OMS_LIFECYCLE_SOURCE_HASH_MISMATCH")
        paths.append(path); hashes.append(spec["sha256"])
    if kind=="option":
        from atlaspm.data.option_history import OptionHistory
        history=OptionHistory(*paths)
    else:
        from atlaspm.data.treasury_history import TreasuryHistory
        history=TreasuryHistory(*paths)
    for path,expected in zip(paths,hashes):
        with path.open("rb") as handle:
            body=handle.read(8*1024*1024+1)
        if len(body)>8*1024*1024 or sha256(body).hexdigest()!=expected:
            raise ValueError("OMS_LIFECYCLE_SOURCE_CHANGED")
    return history,dict(zip(names,hashes))


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
    lifecycle_started=False
    lifecycle_change=0.
    released_credit=0.
    histories={}
    option_deliveries={}
    treasury_states={}
    lifecycle_records=[]

    def pending_stock_value():
        return sum(d["shares"]*d["mark"] for d in option_deliveries.values() if d["state"]=="PENDING")

    def marked_equity() -> tuple[float, list[dict[str, Any]]]:
        valued = [
            _position(raw, account_id) for raw in positions.values()
            if raw["type"] != "equity" or raw["shares"] > 0
        ]
        marked = (
            sum(p["market_value"] for p in valued) + cash + receivable
            - payable - debt - financing_accrual + pending_stock_value()
        )
        return marked, valued

    def risk_snapshot(event_id,leg_id,marked,valued):
        nonlocal halted,halt_reason
        risk_positions=list(valued)
        for delivery in option_deliveries.values():
            if delivery["state"]=="PENDING" and delivery["shares"]:
                risk_positions.append(_position({"type":"equity","identifier":delivery["stock"],
                    "shares":delivery["shares"],"mark_price":delivery["mark"],
                    "mark_source":"synthetic","mark_age_seconds":0},account_id))
        after_fill=post_fill_risk_snapshot(
            risk_positions,marked_net_liquidation=marked,settled_cash=cash,
            unsettled_payable=payable,margin_debt=debt,account_type=account["account_type"],
            max_gross_leverage=plan["policy"]["max_gross_leverage"],
            max_stress_loss_fraction=plan["policy"]["max_stress_loss_fraction"],
            equity_decline_fraction=plan["stress_assumptions"]["equity_decline_fraction"],
            treasury_decline_fraction=plan["stress_assumptions"]["treasury_decline_fraction"])
        post_fill_risk_snapshots.append({"event_id":event_id,"leg_id":leg_id,**after_fill})
        if after_fill["breaches"]:
            post_fill_breaches.extend(after_fill["breaches"])
            halted=True
            halt_reason="Post-fill risk budget breached"

    def asset_lifecycle(e,at):
        nonlocal cash,receivable,expected_equity_change,lifecycle_change,released_credit
        from datetime import date
        from zoneinfo import ZoneInfo
        if (not ledger_payload.get("_synthetic_fixture") or not journal.get("_synthetic_fixture")
                or not str(account_id).startswith("DEMO-") or any(
                    p["mark_source"]!="synthetic" for p in positions.values())):
            raise ValueError("OMS_LIFECYCLE_REAL_DATA_NOT_CERTIFIED")
        if any(o["status"] not in {"FILLED","CANCELED","REJECTED"} for o in orders.values()):
            raise ValueError("OMS_LIFECYCLE_REQUIRES_TERMINAL_ORDERS")
        if set(e)!={"event_id","sequence","timestamp","type","identifier"}:
            raise ValueError("OMS_LIFECYCLE_EVENT_CONTRACT")
        identifier=_text(e["identifier"],"identifier")
        kind=e["type"]
        source="option" if kind.startswith("OPTION_") else "treasury"
        if source not in histories:
            histories[source]=_load_lifecycle_history(journal,source)
        history,hashes=histories[source]
        delta=0.
        record={"identifier":identifier,"type":kind,"source_hashes":hashes}
        if kind=="OPTION_EXPIRY":
            from atlaspm.strategies.options.option_replay import expiry_delivery
            key=("option",identifier)
            pos=positions.get(key)
            terms=history.contracts.get(identifier)
            settlement=history.settlements.get(identifier)
            if pos is None or terms is None or settlement is None or pos["contracts"]<=0 or identifier in option_deliveries:
                raise ValueError("OMS_OPTION_MISSING_LONG_POSITION_OR_SETTLEMENT")
            if (terms.underlying!=pos["underlying"] or terms.right!=pos["right"]
                    or terms.strike!=pos["strike"] or terms.multiplier!=pos["multiplier"]
                    or terms.expiration_at.astimezone(ZoneInfo("America/New_York")).date().isoformat()!=pos["expiration_date"]
                    or at!=max(terms.expiration_at,settlement.available_at)):
                raise ValueError("OMS_OPTION_TERMS_OR_EXPIRY_MISMATCH")
            stock_key=("equity",terms.underlying)
            stock=positions.get(stock_key)
            held=stock["shares"] if stock is not None else 0
            if held!=int(held):
                raise ValueError("OMS_OPTION_FRACTIONAL_UNDERLYING_UNSUPPORTED")
            if any(d["state"]=="PENDING" and d["stock"]==terms.underlying for d in option_deliveries.values()):
                raise ValueError("OMS_OPTION_OVERLAPPING_PHYSICAL_DELIVERY")
            intrinsic,exercised,cash_debit,shares_debit,delivery=expiry_delivery(
                identifier,terms,settlement,int(pos["contracts"]),cash=max(0.,cash-payable),
                underlying_shares=held)
            delta=-_position(pos,account_id)["market_value"]
            if exercised and terms.settlement_type=="physical":
                reference=settlement.settlement_value
                if reference<=0:
                    raise ValueError("OMS_OPTION_INVALID_PHYSICAL_REFERENCE")
                if stock is not None:
                    delta+=held*(reference-stock["mark_price"])
                    stock["mark_price"]=reference
                    stock["mark_age_seconds"]=0
                    stock["shares"]-=shares_debit
                    delta-=shares_debit*reference
                cash-=cash_debit
                delta-=cash_debit
            del positions[key]
            if delivery is not None:
                receivable+=delivery.cash_usd
                delta+=delivery.cash_usd+delivery.shares*settlement.settlement_value
                option_deliveries[identifier]={"state":"PENDING","cash":delivery.cash_usd,
                    "stock":terms.underlying if terms.settlement_type=="physical" else None,
                    "shares":delivery.shares,"mark":settlement.settlement_value,
                    "cash_at":settlement.cash_at.isoformat()}
            else:
                option_deliveries[identifier]={"state":"EXPIRED","cash":0.,"stock":None,"shares":0,
                    "mark":0.,"cash_at":None}
            record.update(exercised=exercised,intrinsic_usd_per_contract=intrinsic,
                          delivery=dict(option_deliveries[identifier]))
        elif kind=="OPTION_DELIVERY":
            delivery=option_deliveries.get(identifier)
            if delivery is None or delivery["state"]!="PENDING" or at.isoformat()!=delivery["cash_at"]:
                raise ValueError("OMS_OPTION_DELIVERY_NOT_DUE_OR_DUPLICATE")
            if receivable+1e-8<delivery["cash"]:
                raise ValueError("OMS_OPTION_RECEIVABLE_MISMATCH")
            receivable-=delivery["cash"]
            cash+=delivery["cash"]
            if delivery["shares"]:
                key=("equity",delivery["stock"])
                pos=positions.get(key)
                if pos is None:
                    pos={"type":"equity","identifier":delivery["stock"],"shares":0,
                         "mark_price":delivery["mark"],"mark_source":"synthetic","mark_age_seconds":0}
                    positions[key]=pos
                if pos["mark_price"]!=delivery["mark"]:
                    raise ValueError("OMS_OPTION_DELIVERY_MARK_CHANGED")
                pos["shares"]+=delivery["shares"]
            delivery["state"]="SETTLED"
            record["delivery"]=dict(delivery)
        else:
            from atlaspm.strategies.bonds.treasury_replay import replay_treasury_holding
            key=("treasury",identifier)
            pos=positions.get(key)
            previous=treasury_states.get(identifier)
            terms=history.terms.get(identifier)
            opening=date.fromisoformat(ledger_payload["valuation_at"][:10])
            day=at.date()
            if terms is None or (pos is None and previous is None) or day<=opening:
                raise ValueError("OMS_TREASURY_TERMS_OR_HOLDING_REQUIRED")
            if previous is not None and day<=date.fromisoformat(previous["date"]):
                raise ValueError("OMS_TREASURY_NONMONOTONIC_OR_DUPLICATE")
            if previous is None:
                if (terms.maturity.isoformat()!=pos["maturity_date"] or terms.annual_coupon_rate!=pos["coupon_rate"]
                        or any(payment.scheduled_on==opening for payment in history.payments[identifier])):
                    raise ValueError("OMS_TREASURY_TERMS_OR_OPENING_ENTITLEMENT_MISMATCH")
                if any(fill["action"]=="BUY_TREASURY" and
                       next(leg["identifier"] for leg in plan["legs"] if leg["leg_id"]==fill["leg_id"])==identifier
                       and datetime.fromisoformat(fill["timestamp"]).date()!=opening for fill in fill_records.values()):
                    raise ValueError("OMS_TREASURY_POST_OPENING_PURCHASE_UNSUPPORTED")
                face=int(pos["face_value"])
                if face!=pos["face_value"]:
                    raise ValueError("OMS_TREASURY_FACE_MISMATCH")
                previous={"date":opening.isoformat(),"face":face,"cash":0.,"receivable":0.}
            face=previous["face"]
            if pos is None and previous["receivable"]==0:
                raise ValueError("OMS_TREASURY_ALREADY_SETTLED")
            replay=replay_treasury_holding(history,identifier,face,[opening,day],include_opening_date_payment=False)
            valuation=replay["valuations"][-1]
            old_value=_position(pos,account_id)["market_value"] if pos is not None else 0.
            cash_change=valuation["cash_available_usd"]-previous["cash"]
            receivable_change=valuation["payment_receivable_usd"]-previous["receivable"]
            if cash_change< -1e-8 or receivable+receivable_change< -1e-8:
                raise ValueError("OMS_TREASURY_CASHFLOW_CONFLICT")
            cash+=cash_change
            receivable+=receivable_change
            delta=cash_change+receivable_change+valuation["dirty_market_value_usd"]-old_value
            if valuation["held_face_usd"]:
                if pos is None:
                    raise ValueError("OMS_TREASURY_MATURED_POSITION_REAPPEARED")
                pos["clean_price_per_100"]=valuation["clean_close_per_100"]
                pos["accrued_interest_per_100"]=valuation["accrued_interest_usd"]*100/face
                pos["modified_duration"]=valuation["dv01_usd"]/(valuation["dirty_market_value_usd"]*.0001)
                pos["mark_age_seconds"]=(day-date.fromisoformat(valuation["quote_date"])).days*86400
            elif pos is not None:
                released_credit+=_position(pos,account_id)["broker_recognized_collateral_value"]
                del positions[key]
            treasury_states[identifier]={"date":day.isoformat(),"face":face,
                "cash":valuation["cash_available_usd"],"receivable":valuation["payment_receivable_usd"]}
            record.update(valuation=valuation,cashflows=replay["cashflows"],
                          cash_delta=cash_change,receivable_delta=receivable_change)
        lifecycle_change+=delta
        expected_equity_change+=delta
        record["net_liquidation_delta"]=delta
        lifecycle_records.append(record)
        return record

    for raw_event in raw_events:
        e = _map(raw_event, "event")
        event_id = _text(e.get("event_id"), "event_id")
        if event_id in seen_events:
            if e != seen_events[event_id]:
                raise ValueError("Conflicting duplicate event_id")
            duplicate_count += 1
            continue
        if halted and not (lifecycle_started and halt_reason=="Post-fill risk budget breached"
                           and e.get("type") in _LIFECYCLE_TYPES):
            raise ValueError(f"{halt_reason}: freeze event replay until reconciliation")
        seq = e.get("sequence")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq <= sequence:
            raise ValueError("Unique journal sequences must be strictly increasing integers")
        timestamp = _timestamp(e.get("timestamp"), "event.timestamp")
        if last_time is not None and timestamp < last_time:
            raise ValueError("Journal timestamps must not move backward")
        sequence, last_time = seq, timestamp
        event_type = e.get("type")
        if event_type in _LIFECYCLE_TYPES:
            lifecycle_started=True
            record=asset_lifecycle(e,timestamp)
            marked,valued=marked_equity()
            gap=(marked-beginning_equity)-expected_equity_change
            if abs(gap)>max(.01,beginning_equity*1e-10):
                raise ValueError("Paper lifecycle ledger failed independent reconciliation")
            risk_snapshot(event_id,None,marked,valued)
            seen_events[event_id]=dict(e)
            audit.append({"event_id":event_id,"sequence":seq,"timestamp":timestamp.isoformat(),
                          "leg_id":None,"type":event_type,"source_event":dict(e),
                          "lifecycle":record,"marked_net_liquidation":marked,"reconciliation_gap":gap,
                          "post_fill_risk_state":post_fill_risk_snapshots[-1]["post_fill_risk_state"]})
            continue
        if lifecycle_started:
            raise ValueError("OMS_NEW_ORDER_EVENTS_AFTER_LIFECYCLE_FORBIDDEN")
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
            risk_snapshot(event_id,leg_id,marked,marked_positions)
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
    if abs(total_credit + released_credit - account["broker_recognized_treasury_collateral_report_only"]) > 1e-8:
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
        **({"asset_lifecycle_records":lifecycle_records,"option_deliveries":option_deliveries,
            "treasury_lifecycle_states":treasury_states} if lifecycle_started else {}),
        "account_replay": {
            "opening_net_liquidation": beginning_equity,
            "marked_net_liquidation": ending_nlv,
            "expected_net_liquidation_change_from_fills_and_fees": expected_equity_change-lifecycle_change,
            **({"expected_net_liquidation_change_from_lifecycle":lifecycle_change,
                "pending_share_delivery_value":pending_stock_value(),
                "released_treasury_collateral_report_only":released_credit} if lifecycle_started else {}),
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
