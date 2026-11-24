"""Broker previews for a reviewed cash-equity draft in an authenticated IBKR Paper session."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from atlaspm.ibkr_equity_snapshot import ACCOUNT, _number, collect_cash_equity_snapshot, selected_paper_account
from atlaspm.execution.ibkr_gateway import DEFAULT_GATEWAY_URL, LocalGatewayTransport, require_private_output
from atlaspm.rebalance_workbench import RunStore


PRICE = re.compile(r"([+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?)(?: USD)?")
STOCK_LOOKUP_SYMBOL = re.compile(r"[A-Z]{1,15}")
STOCK_LOOKUP_BATCH = 100


class PreviewTransport(LocalGatewayTransport):
    def stock_contracts(self, symbols: tuple[str, ...]) -> dict:
        if not 1 <= len(symbols) <= STOCK_LOOKUP_BATCH or any(not STOCK_LOOKUP_SYMBOL.fullmatch(s) for s in symbols):
            raise ValueError("Invalid IBKR stock contract search symbols")
        return self._request("/trsrv/stocks", params={"symbols": ",".join(symbols)})

    def market_snapshot(self, conids: tuple[int, ...]) -> list:
        if not conids or len(conids) > 100 or any(type(conid) is not int or conid <= 0 for conid in conids):
            raise ValueError("IBKR market snapshot requires 1 to 100 positive contract IDs")
        return self._request(
            "/iserver/marketdata/snapshot",
            params={"conids": ",".join(map(str, conids)), "fields": "55,84,86,6509"},
        )

    def what_if(self, account_id: str, ticket: dict) -> dict:
        _require_paper(self, account_id)
        return self._request(f"/iserver/account/{account_id}/orders/whatif", preview={"orders": [ticket]})


def _require_paper(transport, account_id: str) -> None:
    if not ACCOUNT.fullmatch(account_id):
        raise ValueError("Invalid Paper account identifier")
    if selected_paper_account(transport.get("/iserver/accounts")) != account_id:
        raise ValueError("An authenticated IBKR Paper session with the selected account is required")


def live_quotes(transport, contracts: dict[str, int], *, now: datetime | None = None) -> dict[str, dict]:
    """Accept complete current bid/ask marks. Ignore frozen/delayed/partial responses."""
    conids = tuple(contracts.values())
    if len(set(conids)) != len(conids) or any(type(id_) is not int or id_ <= 0 for id_ in conids):
        raise ValueError("Missing or ambiguous contract identifiers")
    for symbol, conid in contracts.items():
        info = transport.get(f"/iserver/contract/{conid}/info")
        if (not isinstance(info, dict) or info.get("conid", info.get("con_id")) != conid
                or info.get("ticker", info.get("local_symbol", info.get("symbol"))) != symbol
                or info.get("secType", info.get("instrument_type")) != "STK" or info.get("currency") != "USD"):
            raise ValueError(f"IBKR contract validation failed for {symbol}")
    quotes: dict[str, dict] = {}
    groups = [conids[i:i + 100] for i in range(0, len(conids), 100)]
    for group in groups:
        for attempt in range(4):
            response = transport.market_snapshot(group)
            if not isinstance(response, list) or not all(isinstance(row, dict) for row in response):
                raise ValueError("Invalid IBKR market-snapshot response")
            by_conid = {row.get("conid"): row for row in response}
            if len(by_conid) != len(response):
                raise ValueError("IBKR market-snapshot duplicate contract")
            observed = now or datetime.now(timezone.utc)
            for symbol, conid in contracts.items():
                if conid not in group:
                    continue
                row = by_conid.get(conid)
                if (not row or row.get("55") != symbol or not str(row.get("6509", "")).startswith("R")
                        or not all(key in row for key in ("84", "86", "_updated"))):
                    continue
                stamp = _number(row["_updated"], "quote timestamp", limit=10**15) / 1000
                age = observed.timestamp() - float(stamp)
                bid, ask = _number(row["84"], "bid"), _number(row["86"], "ask")
                if -3 <= age <= 30 and 0 < bid <= ask:
                    quotes[symbol] = {"bid": float(bid), "ask": float(ask), "updated_epoch": float(stamp)}
            if all(name in quotes for name, conid in contracts.items() if conid in group):
                break
            if attempt != 3:
                time.sleep(.4)
    observed = now or datetime.now(timezone.utc)
    missing = sorted(sym for sym in contracts if sym not in quotes or observed.timestamp() - quotes[sym]["updated_epoch"] > 30)
    if missing:
        raise ValueError(f"Missing current real-time bid/ask: {', '.join(missing)}")
    return quotes


def _money(value: object, label: str) -> Decimal:
    match = PRICE.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"IBKR What-If invalid {label}")
    return _number(match[1].replace(",", ""), label)


def _margin(response: dict, key: str) -> dict:
    values = {field: _money(response[key][field], f"{key}.{field}") for field in ("current", "change", "after")}
    if values["current"] < 0 or values["after"] < 0:
        raise ValueError("IBKR What-If negative margin")
    if abs(values["current"] + values["change"] - values["after"]) > Decimal("1.00"):
        raise ValueError(f"IBKR What-If margin accounting mismatch for {key}")
    return {name: float(value) for name, value in values.items()}


def _ticket(run_id: str, row: dict, conid: int, limit: Decimal) -> dict:
    material = json.dumps([run_id, conid, row["side"], row["shares"], str(limit)], separators=(",", ":"))
    coid = "ATL-" + hashlib.sha256(material.encode()).hexdigest()[:40]
    return {
        "conid": conid, "orderType": "LMT", "side": row["side"], "tif": "DAY",
        "quantity": row["shares"], "price": float(limit), "cOID": coid, "outsideRTH": False,
    }


def broker_estimate(transport, account_id: str, ticket: dict) -> dict:
    response = transport.what_if(account_id, ticket)
    if not isinstance(response, dict) or response.get("warn") or response.get("error"):
        raise ValueError("IBKR What-If returned a warning or error")
    fee = _money(response["amount"]["commission"], "commission")
    if fee < 0:
        raise ValueError("IBKR What-If commission cannot be negative")
    initial, maintenance = (_margin(response, key) for key in ("initial", "maintenance"))
    return {"commission": float(fee), "initial_margin": initial, "maintenance_margin": maintenance}


def _unique_us_stock(groups: object, symbol: str) -> int | None:
    if not isinstance(groups, list) or not all(isinstance(group, dict) for group in groups):
        raise ValueError(f"Malformed IBKR stock lookup for {symbol}")
    matches = set()
    for group in groups:
        if not isinstance(group.get("assetClass"), str):
            raise ValueError(f"Missing IBKR asset class for {symbol}")
        if group.get("assetClass") != "STK":
            continue
        markets = group.get("contracts")
        if not isinstance(markets, list):
            raise ValueError(f"Malformed IBKR contract list for {symbol}")
        for market in markets:
            if not isinstance(market, dict) or type(market.get("isUS")) is not bool:
                raise ValueError(f"Missing IBKR listing country for {symbol}")
            if market["isUS"]:
                conid = market.get("conid")
                if type(conid) is not int or conid <= 0:
                    raise ValueError(f"Invalid IBKR contract identifier for {symbol}")
                matches.add(conid)
    return matches.pop() if len(matches) == 1 else None


def contract_scope(transport, snapshot: dict, mapping: dict[str, int], symbols: set[str]) -> dict[str, int]:
    contracts = dict(snapshot["contracts"])
    for symbol, conid in mapping.items():
        if symbol in contracts and contracts[symbol] != conid:
            raise ValueError(f"Configured contract conflicts with account snapshot for {symbol}")
        contracts[symbol] = conid
    lookup = sorted(symbol for symbol in symbols - contracts.keys() if STOCK_LOOKUP_SYMBOL.fullmatch(symbol))
    for start in range(0, len(lookup), STOCK_LOOKUP_BATCH):
        batch = tuple(lookup[start:start + STOCK_LOOKUP_BATCH])
        response = transport.stock_contracts(batch)
        if not isinstance(response, dict) or response.get("error"):
            raise ValueError("IBKR stock contract lookup failed")
        for symbol in batch:
            conid = _unique_us_stock(response.get(symbol, []), symbol)
            if conid is not None:
                contracts[symbol] = conid
    unresolved = symbols - contracts.keys()
    if unresolved:
        raise ValueError(f"Supply verified IBKR contract mapping for {', '.join(sorted(unresolved))}")
    return {symbol: contracts[symbol] for symbol in sorted(symbols)}


def preview_draft(
    transport, run: dict, snapshot: dict, mapping: dict[str, int], *, now: datetime | None = None,
) -> dict:
    """Preview the exact draft, and block if fresh prices or funds invalidate it."""
    if (run["status"] != "reviewed" or not run["draft"] or not run["input_sha256"].get("market_audit")
            or run.get("account_id_sha256") != hashlib.sha256(snapshot["account_id"].encode()).hexdigest()
            or snapshot.get("is_paper") is not True):
        raise ValueError("A reviewed, market-audited draft for this IBKR Paper account is required")
    _require_paper(transport, snapshot["account_id"])
    symbols = {row["symbol"] for row in run["draft"]}
    contracts = contract_scope(transport, snapshot, mapping, symbols)
    fresh = live_quotes(transport, contracts, now=now)
    current_shares = snapshot["positions"]
    pending_buy_reserve = sum(
        Decimal(str(order["limit_price"])) * order["remaining"] for order in snapshot["open_orders"] if order["side"] == "BUY"
    )
    cash = min(Decimal(str(snapshot["settled_cash"])) - pending_buy_reserve, Decimal(str(snapshot["available_cash"])))
    estimates = []
    reasons = []
    expenses = Decimal(0)
    for row in run["draft"]:
        sym = row["symbol"]
        prior = next(position for position in run["positions"] if position["symbol"] == sym)
        working = {
            side: sum(order["remaining"] for order in snapshot["open_orders"]
                      if order["symbol"] == sym and order["side"] == side)
            for side in ("BUY", "SELL")
        }
        if (prior["held"], prior["pending_buy"], prior["pending_sell"]) != (
            current_shares.get(sym, 0), working["BUY"], working["SELL"],
        ):
            reasons.append(f"{sym}: ACCOUNT_CHANGED_REQUIRES_NEW_REVIEW")
            continue
        # This is a limit-order preview at the reviewed reference; a new
        # order price needs a new risk calculation and review.
        reference = Decimal(str(row["reference_close"]))
        limit = reference.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        mark = Decimal(str(fresh[sym]["ask" if row["side"] == "BUY" else "bid"]))
        if limit <= 0 or abs(reference - limit) > Decimal(".0001") or abs(mark / reference - 1) > Decimal(".005"):
            reasons.append(f"{sym}: FRESH_QUOTE_OUTSIDE_REVIEWED_BAND")
            continue
        ticket = _ticket(run["id"], row, contracts[sym], limit)
        try:
            estimate = broker_estimate(transport, snapshot["account_id"], ticket)
        except (ValueError, KeyError, TypeError) as exc:
            reasons.append(f"{sym}: {exc}")
            continue
        expenses += Decimal(str(estimate["commission"]))
        if row["side"] == "BUY":
            expenses += limit * row["shares"]
        estimates.append({"symbol": sym, "ticket": ticket, "quote": fresh[sym]} | estimate)
    if expenses > cash:
        reasons.append("FRESH_CASH_AFTER_PENDING_BUYS_DOES_NOT_COVER_DRAFT_AND_BROKER_FEES")
    return {
        "schema": "atlaspm_ibkr_whatif_v1", "run_id": run["id"], "as_of": run["as_of"],
        "observed_at": (now or datetime.now(timezone.utc)).isoformat(),
        "status": "blocked" if reasons else "previewed", "account_id_sha256": run["account_id_sha256"],
        "estimates": estimates, "reasons": reasons, "available_cash_bound": float(cash),
        "cash_required": float(expenses), "paper": True, "submitted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--contracts", type=Path, help="Optional JSON mapping of symbols to verified conids")
    parser.add_argument("--url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--ca-file")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-paper-preview", action="store_true", required=True)
    args = parser.parse_args()
    try:
        output = require_private_output(args.output)
        run = RunStore(Path("local_data/rebalance/workbench.sqlite3")).get(args.run)
        if not run or run["status"] != "reviewed":
            raise ValueError("Recorded reviewed run not found")
        mapping = json.loads(args.contracts.read_text(encoding="utf-8")) if args.contracts else {}
        if not isinstance(mapping, dict):
            raise ValueError("Contract mapping must be a JSON object of symbols to contract IDs")
        transport = PreviewTransport(args.url, ca_file=args.ca_file)
        _require_paper(transport, args.account)
        snapshot = collect_cash_equity_snapshot(transport, account_id=args.account, dedicated=True)
        report = preview_draft(transport, run, snapshot, mapping)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
        print(json.dumps({"status": report["status"], "written": str(output), "reasons": report["reasons"]}))
        return 0 if report["status"] == "previewed" else 2
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
