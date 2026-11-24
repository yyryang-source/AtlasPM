"""Normalize a verified loopback IBKR snapshot for dedicated cash equity accounts."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from atlaspm.execution.ibkr_gateway import (
    DEFAULT_GATEWAY_URL,
    LocalGatewayTransport,
    collect_ibkr_readonly_account,
    require_private_output,
)
from atlaspm.rebalance_workbench import Policy, RunStore


WORKING = {"pendingsubmit", "presubmitted", "submitted", "pendingcancel", "precancelled", "partiallyfilled"}
FINAL = {"filled", "cancelled", "apicancelled", "rejected", "expired"}
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,14}")
ACCOUNT = re.compile(r"[A-Za-z0-9_-]{1,64}")


def selected_paper_account(session: object) -> str:
    selected = session.get("selectedAccount") if isinstance(session, dict) else None
    if (not isinstance(selected, str) or not ACCOUNT.fullmatch(selected) or session.get("isPaper") is not True
            or not isinstance(session.get("accounts"), list) or selected not in session["accounts"]):
        raise ValueError("An authenticated IBKR Paper session with the selected account is required")
    return selected


def _number(value: object, name: str, *, limit: int = 10**12) -> Decimal:
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"IBKR missing or invalid {name}") from exc
    if not number.is_finite() or abs(number) > limit:
        raise ValueError(f"IBKR out of range {name}")
    return number


def _quantity(value: object, name: str) -> int:
    number = _number(value, name)
    if not 0 <= number <= 10**9 or number != number.to_integral_value():
        raise ValueError(f"{name} must be a non-negative number of whole shares")
    return int(number)


def _instrument(row: dict, *, cash_currency: str, type_field: str) -> tuple[str, int]:
    symbol, conid = row.get("ticker"), row.get("conid")
    if row.get(type_field) != "STK" or cash_currency != "USD":
        raise ValueError("Snapshot contains unhandled security type or currency")
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise ValueError("IBKR ticker requires an explicit symbol mapping")
    if type(conid) is not int or conid <= 0:
        raise ValueError("IBKR contract identifier is missing")
    return symbol, conid


def _orders(response: object, account: str) -> tuple[list[dict], list[tuple]]:
    if not isinstance(response, dict) or response.get("snapshot") is not True:
        raise ValueError("IBKR did not return a complete live-order snapshot")
    orders = response.get("orders")
    if not isinstance(orders, list) or len(orders) >= 1000:
        raise ValueError("IBKR live orders missing or at the documented result cap")
    active, identities, seen = [], [], set()
    for row in orders:
        if not isinstance(row, dict) or row.get("acct", row.get("account")) != account:
            raise ValueError("IBKR returned orders with missing or different account ownership")
        order_id = row.get("orderId")
        if type(order_id) is not int or order_id <= 0 or order_id in seen:
            raise ValueError("IBKR order identifier missing or repeated")
        seen.add(order_id)
        status = re.sub(r"[\s_-]", "", str(row.get("status", ""))).lower()
        if status not in FINAL | WORKING:
            raise ValueError(f"Unsupported IBKR order status {status!r}")
        if status in FINAL:
            identities.append((order_id, status, str(row.get("filledQuantity")), str(row.get("avgPrice"))))
            continue
        remaining = _quantity(row.get("remainingQuantity"), "remainingQuantity")
        filled = _quantity(row.get("filledQuantity"), "filledQuantity")
        if not remaining:
            raise ValueError("Working IBKR order has no remaining shares")
        if "totalSize" in row and _quantity(row["totalSize"], "totalSize") != remaining + filled:
            raise ValueError("IBKR order size does not reconcile with fills and remaining shares")
        symbol, conid = _instrument(row, cash_currency=row.get("cashCcy"), type_field="secType")
        side = row.get("side")
        if side not in {"BUY", "SELL"} or str(row.get("orderType", "")).upper() not in {"LMT", "LIMIT"}:
            raise ValueError("Working orders must be whole-share equity limit orders")
        # Prices are required explicitly. Never infer from human-readable order text.
        price = _number(row.get("price", row.get("limitPrice")), "open-order limit price")
        if price <= 0:
            raise ValueError("Invalid working order limit price")
        active.append({"id": str(order_id), "symbol": symbol, "conid": conid,
                       "side": side, "remaining": remaining, "limit_price": float(price)})
        identities.append((order_id, status, side, symbol, conid, remaining, filled, price))
    return sorted(active, key=lambda row: int(row["id"])), sorted(identities)


def collect_cash_equity_snapshot(
    transport, *, account_id: str | None, dedicated: bool, now: datetime | None = None,
) -> dict:
    if not dedicated or account_id is not None and not ACCOUNT.fullmatch(account_id):
        raise ValueError("An explicit dedicated cash-equity account and valid ID are required")
    start = now or datetime.now(timezone.utc)
    if start.tzinfo is None:
        raise ValueError("Capture time needs timezone")
    session = transport.get("/iserver/accounts")
    if account_id is None:
        account_id = selected_paper_account(session)
    if (not isinstance(session, dict)
            or session.get("selectedAccount") != account_id
            or not isinstance(session.get("accounts"), list)
            or account_id not in session["accounts"]):
        raise ValueError("Select the intended IBKR account in the authenticated Gateway first")
    _, before = _orders(transport.get("/iserver/account/orders"), account_id)
    capture = collect_ibkr_readonly_account(transport, account_id=account_id)
    raw_account = capture["raw"]["selected_account"]
    if str(raw_account.get("tradingType", "")).upper() != "CASH":
        raise ValueError("IBKR must identify the selected account as CASH")
    summary = transport.get(f"/iserver/account/{account_id}/summary")
    if not isinstance(summary, dict):
        raise ValueError("IBKR account summary missing")
    orders, after = _orders(transport.get("/iserver/account/orders"), account_id)
    if before != after:
        raise ValueError("IBKR orders changed during snapshot; collect after fills settle")
    session_after = transport.get("/iserver/accounts")
    if not isinstance(session_after, dict) or any(
        session_after.get(key) != session.get(key) for key in ("selectedAccount", "isPaper", "sessionId")
    ):
        raise ValueError("IBKR selected account or brokerage session changed during capture")
    observed = now or datetime.now(timezone.utc)
    if observed - start > timedelta(minutes=2):
        raise ValueError("IBKR account snapshot capture exceeded two minutes")
    ledger = capture["raw"]["USD_ledger"]["USD"]
    for currency, balances in capture["raw"]["USD_ledger"].items():
        if currency not in {"BASE", "USD"} and any(
            _number(balances.get(field), f"{currency} {field}") != 0 for field in ("cashbalance", "settledcash")
        ):
            raise ValueError("Non-USD cash balances are incompatible with this cash-account workflow")
    if ledger.get("acctcode") not in (None, account_id):
        raise ValueError("IBKR USD ledger belongs to a different account")
    stamp = _number(ledger.get("timestamp"), "ledger timestamp", limit=10**15)
    if stamp > 10**11:
        stamp /= 1000
    difference = observed.timestamp() - float(stamp)
    if not -10 <= difference <= 120:
        raise ValueError("IBKR USD ledger timestamp is stale or ahead of the capture")
    settled = _number(ledger.get("settledcash"), "settled cash")
    cash = _number(ledger.get("cashbalance"), "cash available for trading")
    funds = _number(summary.get("availableFunds"), "available funds")
    total = summary.get("totalCashValue")
    total_cash = None if total is None else float(_number(total, "total cash"))
    if min(settled, cash, funds) < 0:
        raise ValueError("Negative cash or funds are incompatible with this cash-account workflow")
    positions: dict[str, int] = {}
    contracts: dict[str, int] = {}

    def remember(symbol: str, conid: int) -> None:
        if (symbol in contracts and contracts[symbol] != conid
                or any(name != symbol and id_ == conid for name, id_ in contracts.items())):
            raise ValueError("Conflicting IBKR ticker-to-contract mapping")
        contracts[symbol] = conid

    for row in capture["raw"]["positions"]:
        if row.get("acctId") not in (None, account_id):
            raise ValueError("IBKR position belongs to a different account")
        symbol, conid = _instrument(row, cash_currency=row.get("currency"), type_field="assetClass")
        if symbol in positions:
            raise ValueError("Duplicate IBKR position for the selected account")
        positions[symbol] = _quantity(row.get("position"), "position")
        remember(symbol, conid)
    for order in orders:
        remember(order["symbol"], order["conid"])
    return {
        "schema": "atlaspm_ibkr_cash_equity_snapshot_v1",
        "account_id": account_id, "account_type": "cash", "currency": "USD",
        "is_paper": session.get("isPaper") is True,
        "captured_at": observed.isoformat(), "positions_and_orders_complete": True,
        "managed_equities_only": True, "settled_cash": float(settled),
        "available_cash": float(min(cash, funds, settled)), "total_cash": total_cash, "positions": positions,
        "open_orders": orders, "contracts": contracts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    account = parser.add_mutually_exclusive_group(required=True)
    account.add_argument("--account")
    account.add_argument("--selected-paper-account", action="store_true",
                         help="Use the authenticated Gateway's current Paper account without entering its ID")
    parser.add_argument("--dedicated-cash-equities", action="store_true", required=True)
    parser.add_argument("--allow-local-gateway", action="store_true", required=True)
    parser.add_argument("--url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--ca-file")
    parser.add_argument("--output", type=Path, default=Path("local_data/rebalance/ibkr_equity_snapshot.json"))
    parser.add_argument("--market-audit", type=Path)
    parser.add_argument("--allocation", choices=("equal", "quant"), default="equal")
    args = parser.parse_args()
    try:
        output = require_private_output(args.output)
        if args.market_audit:
            from atlaspm.market_close import approved_inputs

            approved_inputs(args.market_audit)
        transport = LocalGatewayTransport(args.url, ca_file=args.ca_file)
        snapshot = collect_cash_equity_snapshot(transport, account_id=args.account, dedicated=True)
        data = json.dumps(snapshot, indent=2, allow_nan=False).encode()
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_name(f".{uuid4().hex}.part")
        try:
            temp.write_bytes(data)
            temp.replace(output)
        finally:
            temp.unlink(missing_ok=True)
        status = {"written": str(output), "positions": len(snapshot["positions"]),
                  "working_orders": len(snapshot["open_orders"]), "paper": snapshot["is_paper"],
                  "has_total_cash": snapshot["total_cash"] is not None}
        if args.market_audit:
            store = RunStore(Path("local_data/rebalance/workbench.sqlite3"))
            key = store.run_prepared(args.market_audit, data, Policy(allocation=args.allocation),
                                     account_source="local_ibkr_cash_equity_snapshot")
            status.update(id=key, status=store.get(key)["status"])
        print(json.dumps(status))
        return 2 if status.get("status") == "blocked" else 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
