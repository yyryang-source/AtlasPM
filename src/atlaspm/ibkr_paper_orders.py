"""Explicit IBKR Paper submission, cancellation and broker reconciliation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.request import Request

from atlaspm.ibkr_equity_snapshot import _number, _quantity, collect_cash_equity_snapshot
from atlaspm.ibkr_next_day import next_day_review
from atlaspm.ibkr_whatif import PreviewTransport, _money, _require_paper
from atlaspm.paper_order_journal import PaperJournal
from atlaspm.execution.ibkr_gateway import DEFAULT_GATEWAY_URL, GatewayHTTPError, require_private_output
from atlaspm.rebalance_workbench import RunStore, _json, next_session_window


BROKER_STATUS = {
    "pendingsubmit": "working", "presubmitted": "working", "submitted": "working",
    "partiallyfilled": "partial", "pendingcancel": "cancel_requested", "precancelled": "cancel_requested",
    "filled": "filled", "cancelled": "cancelled", "apicancelled": "cancelled", "expired": "expired",
    "rejected": "rejected", "inactive": "unknown", "warnstate": "confirmation_required",
}
TERMINAL = {"filled", "cancelled", "rejected", "expired"}
SIDES = {"B": "BUY", "S": "SELL", "BUY": "BUY", "SELL": "SELL"}


def _broker_status(value: object) -> str:
    return BROKER_STATUS.get(re.sub(r"[\s_-]", "", str(value)).lower(), "unknown")


def _order_id(value: object) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,18}", str(value)):
        raise ValueError("IBKR did not return a positive order ID")
    return int(value)


def _check_ticket(ticket: dict) -> None:
    if (set(ticket) != {"conid", "orderType", "side", "tif", "quantity", "price", "cOID", "outsideRTH"}
            or ticket["orderType"] != "LMT" or ticket["side"] not in {"BUY", "SELL"}
            or ticket["tif"] != "DAY" or ticket["outsideRTH"] is not False
            or not re.fullmatch(r"ATL-[0-9a-f]{40}", str(ticket["cOID"]))
            or type(ticket["quantity"]) is not int or not _quantity(ticket["quantity"], "order quantity")
            or type(ticket["price"]) not in {float, int} or _number(ticket["price"], "order price") <= 0
            or type(ticket["conid"]) is not int or ticket["conid"] <= 0):
        raise ValueError("Expected an AtlasPM whole-share DAY limit ticket")


class PaperTransport(PreviewTransport):
    def place(self, account_id: str, ticket: dict) -> object:
        _check_ticket(ticket)
        _require_paper(self, account_id)
        return self._send_json(Request(
            self.base_url + f"/iserver/account/{account_id}/orders",
            data=json.dumps({"orders": [ticket]}, allow_nan=False).encode(),
            method="POST", headers={"Accept": "application/json", "Content-Type": "application/json"},
        ))

    def cancel(self, account_id: str, order_id: int) -> object:
        order_id = _order_id(order_id)
        _require_paper(self, account_id)
        return self._send_json(Request(
            self.base_url + f"/iserver/account/{account_id}/order/{order_id}", method="DELETE",
            headers={"Accept": "application/json"},
        ))


def _within_review(fresh: dict, reviewed: dict) -> bool:
    approved_contracts = reviewed.get("next_day_contracts")
    if approved_contracts is not None and fresh["next_day_contracts"] != approved_contracts:
        return False
    current, previous = fresh["next_day_snapshot"], reviewed["next_day_snapshot"]
    if any(current[key] != previous[key] for key in ("positions", "open_orders")):
        return False
    if any(current.get(key) is None or previous.get(key) is None
           or abs(Decimal(str(current[key])) - Decimal(str(previous[key]))) > Decimal(".01")
           for key in ("settled_cash", "available_cash", "total_cash")):
        return False
    current_orders, previous_orders = fresh["broker_estimates"], reviewed["broker_estimates"]
    if len(current_orders) != len(previous_orders):
        return False
    for candidate, approved in zip(current_orders, previous_orders, strict=True):
        if candidate["symbol"] != approved["symbol"] or candidate["ticket"] != approved["ticket"]:
            return False
        costs = [(candidate[field]["change"], approved[field]["change"])
                 for field in ("initial_margin", "maintenance_margin")]
        costs.append((candidate["commission"], approved["commission"]))
        if any(Decimal(str(current)) - Decimal(str(previous)) > Decimal(".01") for current, previous in costs):
            return False
    return True


def _placement(response: object) -> tuple[str, int | None, str]:
    if isinstance(response, list) and len(response) == 1 and isinstance(response[0], dict):
        reply = response[0]
        if "id" in reply and "message" in reply and "order_id" not in reply:
            return "confirmation_required", None, "IBKR returned an order warning. Check the broker interface."
        order_id = reply.get("order_id", reply.get("orderId"))
        if order_id is not None:
            return _broker_status(reply.get("order_status")), _order_id(order_id), "Broker acknowledged ticket"
    if isinstance(response, dict) and isinstance(response.get("error"), str) and response["error"]:
        detail = response["error"]
        if not re.search(r"duplicate|already|timeout", detail, re.I):
            return "rejected", None, detail
    return "unknown", None, "Unrecognized placement reply. Reconcile with the broker before further orders."


def submit(
    transport, journal: PaperJournal, reviewed: dict, original: dict, account_id: str,
    mapping: dict[str, int], *, now: datetime | None = None,
) -> dict:
    if (reviewed["status"] != "reviewed" or reviewed.get("parent_id") != original["id"]
            or not reviewed.get("broker_estimates") or any(not check["passed"] for check in reviewed["checks"])
            or reviewed.get("account_id_sha256") != hashlib.sha256(account_id.encode()).hexdigest()):
        raise ValueError("A reviewed next-day plan for this Paper account is required")
    if journal.get(reviewed["id"]):
        raise ValueError("This Paper batch already exists. Use reconcile to recover order status.")
    reviewed_limits = {(row["symbol"], row["ticket"]["side"]): Decimal(str(row["ticket"]["price"]))
                       for row in reviewed["broker_estimates"]}
    fresh = next_day_review(transport, original, account_id, mapping, now=now, reviewed_limits=reviewed_limits)
    if fresh["status"] != "review" or not _within_review(fresh, reviewed):
        return {"status": "review_required", "review": fresh}
    if fresh["next_day_snapshot"].get("total_cash") is None:
        raise ValueError("The broker total cash observation is required to reconcile this batch")
    for leg in fresh["broker_estimates"]:
        _check_ticket(leg["ticket"])
    _, _, session_close = next_session_window(date.fromisoformat(original["as_of"]))
    deadline = min(session_close.timestamp(), *(row["updated_epoch"] + 30 for row in fresh["next_day_quotes"].values()))
    if (now or datetime.now(timezone.utc)).timestamp() > deadline:
        raise ValueError("Realtime quotes expired during broker preview; re-run next-day checks")
    journal.claim(reviewed, fresh)
    for leg in fresh["broker_estimates"]:
        if (now or datetime.now(timezone.utc)).timestamp() > deadline:
            break
        ticket = leg["ticket"]
        client_id = ticket["cOID"]
        journal.transition(client_id, "sending", "Paper submit intent recorded", previous={"not_sent"})
        try:
            state, broker_id, message = _placement(transport.place(account_id, ticket))
        except (OSError, ValueError, TimeoutError) as exc:
            state, broker_id, message = "unknown", None, str(exc)
        journal.transition(client_id, state, message, previous={"sending"}, order_id=broker_id,
                           broker_filled=0 if state == "rejected" and broker_id is None else None)
        if state not in {"working", "partial", "filled"}:
            break
    return {"status": "journaled", "batch": journal.get(reviewed["id"])}


def _terminal_order(transport, order: dict, account: str, batch: dict) -> dict | None:
    broker_id = _order_id(order["order_id"])
    try:
        row = transport.get(f"/iserver/account/order/status/{broker_id}")
    except GatewayHTTPError as exc:
        if exc.status == 503:
            return None
        raise
    if not isinstance(row, dict) or "order_status" not in row:
        raise ValueError("Malformed individual IBKR order status")
    if _broker_status(row["order_status"]) not in TERMINAL:
        return None
    ticket = order["ticket"]
    order_time = row.get("order_time")
    if not isinstance(order_time, str) or not re.fullmatch(r"[0-9]{12}", order_time):
        raise ValueError("Individual IBKR order status lacks a UTC order timestamp")
    placed = datetime.strptime(order_time, "%y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    started = datetime.fromisoformat(batch["created_at"])
    if (_order_id(row.get("order_id")) != broker_id or row.get("account") != account
            or row.get("conid") != ticket["conid"] or row.get("symbol") != order["symbol"]
            or SIDES.get(str(row.get("side", "")).upper()) != ticket["side"] or row.get("sec_type") != "STK"
            or row.get("currency") != "USD" or row.get("tif") != ticket["tif"]
            or str(row.get("order_type", "")).upper() not in {"LMT", "LIMIT"}
            or row.get("order_ref") not in (None, "", order["client_id"])
            or _quantity(row.get("total_size"), "broker total size") != ticket["quantity"]
            or placed.date().isoformat() != batch["session"] or placed.timestamp() < started.timestamp() - 1):
        raise ValueError("Individual IBKR order status conflicts with the journaled Paper ticket")
    return {"order_ref": order["client_id"], "orderId": broker_id, "acct": account,
            "conid": row["conid"], "side": ticket["side"], "status": row["order_status"],
            "filledQuantity": row.get("cum_fill"), "remainingQuantity": row.get("size")}


def _observed_orders(transport, batch: dict, account: str) -> list[dict]:
    orders = transport.get("/iserver/account/orders")
    if (not isinstance(orders, dict) or orders.get("snapshot") is not True
            or not isinstance(orders.get("orders"), list) or len(orders["orders"]) >= 1000
            or not all(isinstance(row, dict) for row in orders["orders"])):
        raise ValueError("Broker order snapshot is incomplete")
    rows = list(orders["orders"])
    ids = {row.get("orderId") for row in rows if type(row.get("orderId")) is int}
    references = {row.get("order_ref") for row in rows if isinstance(row.get("order_ref"), str)}
    for order in batch["orders"]:
        if (order["order_id"] is None or order["order_id"] in ids or order["client_id"] in references
                or order["state"] in TERMINAL | {"not_sent"}):
            continue
        recovered = _terminal_order(transport, order, account, batch)
        if recovered is not None:
            rows.append(recovered)
    return rows


def _broker_observations(batch: dict, orders: list[dict], trades: object, account: str) -> tuple[list[dict], list[dict]]:
    if not isinstance(trades, list):
        raise ValueError("Broker trade snapshot is incomplete")
    indexed = {order["client_id"]: order for order in batch["orders"]}
    id_index = {order["order_id"]: order["client_id"] for order in batch["orders"] if order["order_id"] is not None}
    if len(id_index) != sum(order["order_id"] is not None for order in batch["orders"]):
        raise ValueError("Broker order ID is shared by multiple journaled tickets")
    observations = []
    seen: set[str] = set()
    observed_ids: set[int] = set()
    for row in orders:
        reference = row.get("order_ref")
        broker_id = row.get("orderId")
        existing = id_index.get(broker_id) if type(broker_id) is int else None
        if reference not in (None, "") and existing is not None and reference != existing:
            raise ValueError("Broker order ID and client reference identify different tickets")
        client_id = reference if reference in indexed else existing
        if client_id is None:
            continue
        order = indexed[client_id]
        ticket = order["ticket"]
        broker_id = _order_id(broker_id)
        if (client_id in seen or broker_id in observed_ids or order["state"] == "not_sent"
                or row.get("acct", row.get("account")) != account
                or row.get("conid") != ticket["conid"] or row.get("side") != ticket["side"]):
            raise ValueError("Broker order identity conflicts with the journaled Paper ticket")
        seen.add(client_id)
        observed_ids.add(broker_id)
        filled = _quantity(row.get("filledQuantity"), "broker filled quantity")
        remaining = _quantity(row.get("remainingQuantity"), "broker remaining quantity")
        state = _broker_status(row.get("status"))
        if (filled + remaining > ticket["quantity"] or state == "filled" and filled != ticket["quantity"]
                or state in {"working", "partial", "cancel_requested"} and filled + remaining != ticket["quantity"]):
            raise ValueError("Broker fill quantities conflict with the journaled Paper ticket")
        if order["state"] in TERMINAL and order["state"] != state:
            raise ValueError("Broker observation conflicts with a terminal Paper order state")
        if state == "working" and filled:
            state = "partial"
        detail = "Verified broker order observation"
        if order["state"] in {"cancel_requested", "cancel_unknown"} and state in {"working", "partial"}:
            # A Submitted observation can arrive after DELETE was accepted.
            # Retain any new fill evidence without clearing the cancellation.
            state = order["state"]
            detail = "Working order remains visible while cancellation is being reconciled"
        observations.append({"client_id": client_id, "state": state, "detail": detail,
                             "order_id": broker_id, "broker_filled": filled, "expected": {order["state"]}})
    fills = []
    started = datetime.fromisoformat(batch["created_at"]).timestamp()
    for row in trades:
        if not isinstance(row, dict):
            raise ValueError("Malformed broker execution")
        reference = row.get("order_ref")
        if reference not in indexed:
            continue
        order = indexed[reference]
        ticket = order["ticket"]
        side = SIDES.get(str(row.get("side", "")).upper())
        execution_id = row.get("execution_id")
        if (order["state"] == "not_sent" or row.get("account", row.get("accountCode")) != account
                or row.get("conid") != ticket["conid"] or side != ticket["side"]
                or not isinstance(execution_id, str) or not execution_id):
            raise ValueError("Broker execution identity conflicts with the journaled Paper ticket")
        stamp = _number(row.get("trade_time_r"), "execution timestamp", limit=10**15) / 1000
        if float(stamp) < started - 1:
            raise ValueError("Broker execution predates the Paper batch")
        shares = _quantity(row.get("size"), "execution quantity")
        price = _money(row["price"], "execution price")
        limit = Decimal(str(ticket["price"]))
        if (not 0 < shares <= ticket["quantity"] or price <= 0 or side == "BUY" and price > limit
                or side == "SELL" and price < limit):
            raise ValueError("Broker execution exceeds the Paper ticket's limit or quantity")
        fee = None if row.get("commission") in (None, "") else _money(row["commission"], "execution commission")
        if fee is not None and fee < 0:
            raise ValueError("Execution commission cannot be negative")
        fills.append({"execution_id": execution_id, "client_id": reference, "symbol": order["symbol"],
                      "side": side, "shares": shares, "price": str(price), "commission": str(fee) if fee is not None else None})
    return observations, fills


def _reconcile_account(batch: dict, snapshot: dict) -> dict:
    expected = dict(batch["baseline"]["positions"])
    baseline = batch["baseline"]["total_cash"]
    cash = Decimal(str(baseline))
    fees_known = True
    quantities: dict[str, int] = {}
    for fill in batch["fills"]:
        shares = fill["shares"]
        direction = 1 if fill["side"] == "BUY" else -1
        quantities[fill["client_id"]] = quantities.get(fill["client_id"], 0) + shares
        expected[fill["symbol"]] = expected.get(fill["symbol"], 0) + direction * shares
        cash -= direction * shares * Decimal(fill["price"])
        if fill["commission"] is None:
            fees_known = False
        else:
            cash -= Decimal(fill["commission"])
    filled_as_reported = True
    for order in batch["orders"]:
        filled = quantities.get(order["client_id"], 0)
        if filled > order["ticket"]["quantity"]:
            raise ValueError("Duplicate or excessive fills in the Paper execution ledger")
        if order["broker_filled"] is None and order["state"] != "not_sent":
            filled_as_reported = False
        if order["broker_filled"] is not None and filled != order["broker_filled"]:
            filled_as_reported = False
    positions = snapshot["positions"]
    differences = {symbol: positions.get(symbol, 0) - expected.get(symbol, 0)
                   for symbol in sorted(expected.keys() | positions.keys())
                   if positions.get(symbol, 0) != expected.get(symbol, 0)}
    actual = snapshot["total_cash"]
    cash_difference = None if not fees_known or actual is None else Decimal(str(actual)) - cash
    finished = all(order["state"] in TERMINAL | {"not_sent"} for order in batch["orders"])
    if not finished or not filled_as_reported or cash_difference is None:
        status = "pending"
    else:
        status = "matched" if not differences and abs(cash_difference) <= Decimal(".01") else "mismatch"
    return {"status": status, "observed_at": snapshot["captured_at"], "positions_expected": expected,
            "positions_observed": positions, "position_differences": differences, "cash_expected": float(cash) if fees_known else None,
            "cash_observed": actual, "cash_difference": float(cash_difference) if cash_difference is not None else None,
            "fills_confirmed": filled_as_reported, "working_or_uncertain": not finished}


def reconcile(transport, journal: PaperJournal, batch_id: str, account: str) -> dict:
    batch = journal.get(batch_id)
    if batch is None or batch["account_hash"] != hashlib.sha256(account.encode()).hexdigest():
        raise ValueError("Paper batch not found for this account")
    _require_paper(transport, account)
    orders = _observed_orders(transport, batch, account)
    trades = transport.get("/iserver/account/trades")
    snapshot = collect_cash_equity_snapshot(transport, account_id=account, dedicated=True)
    if snapshot.get("is_paper") is not True:
        raise ValueError("Paper session changed during reconciliation")
    after = _observed_orders(transport, batch, account)
    observations, fills = _broker_observations(batch, orders, trades, account)
    latest, _ = _broker_observations(batch, after, trades, account)
    if sorted(observations, key=lambda item: item["client_id"]) != sorted(latest, key=lambda item: item["client_id"]):
        raise ValueError("Broker orders changed during reconciliation; poll again")
    journal.apply(batch_id, observations, fills)
    result = _reconcile_account(journal.get(batch_id), snapshot)
    result["observed_working_client_ids"] = [item["client_id"] for item in latest
                                             if item["state"] in {"working", "partial"}]
    return journal.save_reconciliation(batch_id, result)


def cancel(transport, journal: PaperJournal, batch_id: str, account_id: str, client_id: str) -> dict:
    result = reconcile(transport, journal, batch_id, account_id)
    batch = journal.get(batch_id)
    order = next((order for order in batch["orders"] if order["client_id"] == client_id), None)
    if not order or order["state"] not in {"working", "partial"} or client_id not in result.get("observed_working_client_ids", []):
        raise ValueError("No matching working broker order is available for cancellation")
    journal.transition(client_id, "cancel_requested", "Paper cancellation intent recorded", previous={order["state"]})
    try:
        response = transport.cancel(account_id, order["order_id"])
        if (not isinstance(response, dict) or response.get("error") or not response.get("msg")
                or _order_id(response.get("order_id")) != order["order_id"]):
            raise ValueError("Unconfirmed broker response to the cancellation request")
    except (OSError, ValueError, TimeoutError) as exc:
        journal.transition(client_id, "cancel_unknown", str(exc), previous={"cancel_requested"})
        return {"status": "cancel_unknown", "client_id": client_id}
    return {"status": "cancel_requested", "client_id": client_id}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("submit", "reconcile", "cancel"))
    parser.add_argument("--run", required=True, help="Reviewed next-day run identifier")
    parser.add_argument("--account", required=True)
    parser.add_argument("--confirm-run", help="Enter the reviewed run ID again to transmit a Paper batch")
    parser.add_argument("--client-id", help="Journaled client ID of the Paper order to cancel")
    parser.add_argument("--contracts", type=Path, help="Optional JSON mapping of symbols to verified conids")
    parser.add_argument("--url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--ca-file")
    parser.add_argument("--output", type=Path, help="Optional reconciliation report under local_data")
    parser.add_argument("--watch", action="store_true", help="Poll Paper reconciliation until terminal")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    if (args.watch and args.action != "reconcile") or not 30 <= args.poll_seconds <= 3600:
        parser.error("--watch requires reconcile and a polling interval from 30 to 3600 seconds")
    if args.output and args.action != "reconcile":
        parser.error("--output is scoped to reconciliation reports")
    try:
        output = require_private_output(args.output) if args.output else None
        db = Path("local_data/rebalance/workbench.sqlite3")
        store, journal = RunStore(db), PaperJournal(db)
        transport = PaperTransport(args.url, ca_file=args.ca_file)
        if args.action == "submit":
            if args.confirm_run != args.run:
                raise ValueError("Submitting a Paper batch requires --confirm-run equal to --run")
            reviewed = store.get(args.run)
            original = store.get(reviewed["parent_id"]) if reviewed and reviewed.get("parent_id") else None
            if original is None:
                raise ValueError("Reviewed next-day run not found")
            mapping = json.loads(args.contracts.read_text(encoding="utf-8")) if args.contracts else {}
            if not isinstance(mapping, dict):
                raise ValueError("Contract mapping must be a JSON object")
            outcome = submit(transport, journal, reviewed, original, args.account, mapping)
            if outcome["status"] == "review_required":
                key = store.record_next_day(outcome["review"])
                result = {"status": "review_required", "next_day_run": key}
            else:
                orders = [{"client_id": row["client_id"], "state": row["state"]}
                          for row in outcome["batch"]["orders"]]
                healthy = all(row["state"] in {"working", "partial", "filled"} for row in orders)
                result = {"status": "journaled" if healthy else "needs_reconciliation",
                          "run": args.run, "orders": orders}
        elif args.action == "cancel":
            if not args.client_id:
                raise ValueError("Cancelling a single Paper order requires --client-id")
            result = cancel(transport, journal, args.run, args.account, args.client_id)
        else:
            batch = journal.get(args.run)
            if batch is None or batch["account_hash"] != hashlib.sha256(args.account.encode()).hexdigest():
                raise ValueError("Paper batch not found for this account")
            while True:
                try:
                    result = reconcile(transport, journal, args.run, args.account)
                except (OSError, ValueError) as exc:
                    if not args.watch:
                        raise
                    print(_json({"status": "waiting_for_broker", "run": args.run, "error": str(exc)}), flush=True)
                else:
                    if not args.watch or result["status"] != "pending":
                        break
                    print(_json({"status": "pending", "run": args.run}), flush=True)
                time.sleep(args.poll_seconds)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as handle:
                handle.write(_json(result) + "\n")
        if args.action == "reconcile":
            print(_json({"status": result["status"], "run": args.run,
                         "position_mismatches": len(result["position_differences"])}))
        else:
            print(_json(result))
        return 0 if result["status"] in {"journaled", "cancel_requested", "matched"} else 2
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(_json({"status": "blocked", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
