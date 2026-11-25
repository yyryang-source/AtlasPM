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

from atlaspm.execution.ibkr_equity_snapshot import ACCOUNT, _number, collect_cash_equity_snapshot, selected_paper_account
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


class ProfileCheckError(ValueError):
    """Safe diagnostic stage; never include broker response or account selector."""
    def __init__(self, stage, reason, **metadata):
        super().__init__(reason)
        self.stage = stage
        self.reason = reason
        self.metadata = metadata


def _profile_stage(stage, fn, *args, **kwargs):
    from atlaspm.execution.ibkr_gateway import GatewayHTTPError
    try:
        return fn(*args, **kwargs)
    except ProfileCheckError:
        raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        # Only known internal codes may be copied; broker strings may contain
        # identifiers, balances or other confidential fields.
        known = {
            "PAPER_PREFLIGHT_IDENTITY_REQUIRED", "PAPER_PREFLIGHT_SELECTED_ACCOUNT_MISMATCH",
            "PAPER_PREFLIGHT_ACCOUNT_MISMATCH", "PAPER_SESSION_NOT_READY",
            "PAPER_SSO_NOT_VALIDATED", "PAPER_LOGIN_TYPE_NOT_VERIFIED",
            "PAPER_DEMO_ACCOUNT_NOT_VERIFIED", "PAPER_HEARTBEAT_NOT_READY",
            "PAPER_PROFILE_INVALID", "PAPER_PROFILE_CA_FILE_NOT_FOUND",
            "PAPER_PROFILE_SIZE_LIMIT", "PAPER_SSO_PACING_STATE_INVALID",
            "PAPER_SSO_CLOCK_REVIEW_REQUIRED",
        }
        reason = (f"HTTP_{exc.status}" if isinstance(exc, GatewayHTTPError)
                  else str(exc) if str(exc) in known else type(exc).__name__)
        raise ProfileCheckError(stage, reason) from None


def _profile_selected_account(session, account_id):
    if not isinstance(session, dict):
        raise ProfileCheckError("SELECTED_ACCOUNT", "ACCOUNT_SESSION_NOT_OBJECT")
    accounts = session.get("accounts")
    selected = session.get("selectedAccount")
    if (selected != account_id or not isinstance(accounts, list)
            or account_id not in accounts
            or ("isPaper" in session and session["isPaper"] is not True)):
        raise ProfileCheckError("SELECTED_ACCOUNT", "PAPER_PREFLIGHT_SELECTED_ACCOUNT_MISMATCH",
                                selected_field_present="selectedAccount" in session,
                                selected_matches=selected == account_id,
                                account_list_present=isinstance(accounts, list),
                                account_list_contains_profile=isinstance(accounts, list) and account_id in accounts,
                                paper_flag_present="isPaper" in session,
                                paper_flag_contradicts="isPaper" in session and session["isPaper"] is not True)


def _complete_profile_orders(transport, account_id):
    """Documented order-list preflight followed by a separate unfiltered read.

    The first force refresh may return a blank array. It is never an order
    snapshot and must not establish that the account has no working orders.
    Shared transport owns endpoint pacing, including the second GET.
    """
    from atlaspm.execution.ibkr_equity_snapshot import _orders
    _profile_stage("ORDER_LIST_INITIALIZE", transport._request,
                   "/iserver/account/orders", params={"force":"true"})
    response = _profile_stage("ORDER_LIST_READ", transport.get, "/iserver/account/orders")
    obj = isinstance(response, dict)
    rows = response.get("orders") if obj else None
    flag = response.get("snapshot") if obj else None
    metadata = {"response_kind":"object" if obj else "array" if isinstance(response,list) else "other",
                "snapshot_flag":"true" if flag is True else "false" if flag is False else "missing_or_invalid",
                "orders_array_present":isinstance(rows,list),
                "returned_order_count":len(rows) if isinstance(rows,list) else None,
                "broker_error_present":obj and bool(response.get("error"))}
    if not obj or flag is not True or not isinstance(rows,list) or len(rows)>=1000 or metadata["broker_error_present"]:
        raise ProfileCheckError("ORDER_LIST_VALIDATE", "COMPLETE_ORDER_SNAPSHOT_REQUIRED", **metadata)
    try:
        _orders(response, account_id)
        # Reject conflicting ownership aliases; do not infer ownership.
        if any(any(row[key] != account_id for key in ("acct","account") if key in row) for row in rows):
            raise ValueError("conflicting ownership")
    except (ValueError,TypeError,KeyError,OverflowError):
        raise ProfileCheckError("ORDER_LIST_VALIDATE", "ORDER_ROWS_UNVERIFIED", **metadata) from None
    return response


def paper_profile_preflight(capture, summary, session, orders, *, now):
    """Real Paper account risk inputs, never a verified financing ledger.

    A complete order snapshot is essential: do not assume no open orders
    from an empty positions list. The raw account can be STKNOPT or CASH.
    This check does not infer the account's margin type or borrowing limit.
    """
    from atlaspm.execution.ibkr_equity_snapshot import _orders
    from atlaspm.risk.ibkr_margin_monitor import normalize_ibkr_margin_summary
    from atlaspm.engine.contracts import finite, timestamp

    account_id = capture.get("account_id")
    if (not isinstance(account_id, str) or not re.fullmatch(r"DU(?:R)?[0-9]{1,20}", account_id)
            or capture.get("source") != "local_authenticated_ibkr_gateway"
            or any(capture.get(k) is not True for k in ("paper_identity_verified",
                "session_reported_authenticated", "gateway_tls_certificate_validated"))
            or capture.get("live_execution_enabled") is not False
            or capture.get("broker_order_submission_enabled") is not False):
        raise ValueError("PAPER_PREFLIGHT_IDENTITY_REQUIRED")
    _profile_selected_account(session, account_id)
    raw = capture["raw"]
    selected = raw["selected_account"]
    if (selected.get("type") != "DEMO" or selected.get("currency") != "USD"
            or selected.get("brokerageAccess") is not True
            or selected.get("accountId", selected.get("id")) != account_id
            or any(selected[k] != account_id for k in ("accountId","id") if k in selected)):
        raise ValueError("PAPER_PREFLIGHT_ACCOUNT_MISMATCH")
    active, _ = _orders(orders, account_id)
    margin = normalize_ibkr_margin_summary(summary, capture, now_utc=now)
    cash = finite(raw["USD_ledger"]["USD"]["settledcash"], "settled cash")
    nlv = finite(margin["margin_summary_usd_claim"]["total_nlv"], "reported NLV", minimum=.01)
    available = finite(margin["margin_summary_usd_claim"]["available_funds"], "available funds")
    excess = finite(margin["margin_summary_usd_claim"]["excess_liquidity"], "excess liquidity")
    age = (now-timestamp(capture["captured_at_utc"], "captured_at")).total_seconds()
    reasons = set(margin["data_integrity_block_reasons"]) | set(margin["risk_alerts"])
    if not 0 <= age <= 120:
        reasons.add("PAPER_PREFLIGHT_CAPTURE_STALE")
    if active:
        reasons.add("ACTIVE_ORDERS_REQUIRE_RECONCILIATION")
    if cash <= 0 or available <= 0 or excess <= 0:
        reasons.add("NONPOSITIVE_CASH_OR_MARGIN_BUFFER")
    # Reserve cash, but never infer debt from settledcash or NLV residual.
    # No margin borrowing, uncovered sell, or unknown open-order exposure.
    cash_bound = max(0., min(cash, available, excess))
    return {"account_id":account_id, "margin":margin, "reasons":sorted(reasons),
            "active_order_count":len(active), "capture_age_seconds":age,
            "cash_bound_usd":cash_bound, "reported_nlv_usd":nlv,
            "account_trading_type_reported":selected.get("tradingType"),
            "financing_verified":False, "collateral_verified":False}


def _profile_ticket(account_id, identity, limit_price, shares):
    """A single explicitly supplied hypothetical BUY; no order submission."""
    if type(shares) is not int or not 1 <= shares <= 10:
        raise ValueError("PREVIEW_SHARE_LIMIT")
    price = _number(limit_price, "preview price")
    if price <= 0 or price != price.quantize(Decimal(".01")) or price*shares > 2500:
        raise ValueError("PREVIEW_NOTIONAL_OR_TICK_LIMIT")
    if identity.get("currency") != "USD" or identity.get("asset_class") != "STK" or identity.get("broker_identity_checked") is not True:
        raise ValueError("PREVIEW_EQUITY_IDENTITY_REQUIRED")
    cid = identity.get("conid")
    if type(cid) is not int or cid <= 0:
        raise ValueError("PREVIEW_CONID_REQUIRED")
    # Distinct preview-only client identifier; never a reviewed workbench run.
    nonce = hashlib.sha256(f"{account_id}:{cid}:{shares}:{price}:{time.time_ns()}".encode()).hexdigest()[:32]
    return {"conid":cid,"orderType":"LMT","side":"BUY","tif":"DAY",
            "quantity":shares,"price":float(price),"cOID":"ATL-WHATIF-"+nonce,"outsideRTH":False}


def profile_preview_check(*, symbol=None, limit_price=None, shares=1, allow_preview=False):
    """Saved-profile risk diagnostics; optional isolated exact-ticket What-If.

    No market subscriptions in check-only mode. An explicit ticket, current
    quote candidate and clean margin monitor are required before preview POST.
    The existing submission guard is intentionally untouched.
    """
    from atlaspm.execution.ibkr_gateway import (
        _paper_sync_lock, load_paper_profile, _paper_sso_cooldown,
        _write_sync_json, _PAPER_SSO_ATTEMPT, _sync_path,
        collect_ibkr_readonly_account, GatewayHTTPError,
    )
    from atlaspm.execution.ibkr_market_data import MarketPool, resolve_us_equities, PAPER_MARKET_RESERVATION
    from atlaspm.research.financial_events import FLAGS
    from atlaspm.framework.orchestrator import market_phase
    import sys

    if type(allow_preview) is not bool or (symbol is None) != (limit_price is None):
        raise ValueError("PREVIEW_REQUIRES_EXPLICIT_SYMBOL_AND_LIMIT")
    if symbol is None and (allow_preview or shares != 1):
        raise ValueError("CHECK_ONLY_DOES_NOT_ACCEPT_TICKET_FLAGS")
    if symbol is not None:
        if not allow_preview:
            raise ValueError("EXPLICIT_PREVIEW_PERMISSION_REQUIRED")
        # Validate numeric bounds before touching the broker.
        _profile_ticket("DU0", {"conid":1,"currency":"USD","asset_class":"STK",
                               "broker_identity_checked":True}, limit_price, shares)
        from atlaspm.data.provider_policy import local_rights
        local_rights("ibkr")
    def progress(message):
        print("[IBKR Risk] "+message, file=sys.stderr, flush=True)
    with _paper_sync_lock():
        marker = _sync_path(PAPER_MARKET_RESERVATION)
        if marker.exists():
            raise ValueError("PREVIOUS_MARKET_RESERVATION_UNCERTAIN")
        profile = _profile_stage("LOCAL_PROFILE", load_paper_profile)
        account_id = profile["account"]
        transport = _profile_stage("TLS_TRANSPORT", LocalGatewayTransport, profile["url"], ca_file=profile["ca_file"])
        _profile_stage("SSO_PACING", _paper_sso_cooldown, progress)
        _profile_stage("SSO_PACING_WRITE", _write_sync_json, _PAPER_SSO_ATTEMPT,
                       {"attempted_at_utc":datetime.now(timezone.utc).isoformat()})
        capture = _profile_stage("PAPER_ACCOUNT_CAPTURE", collect_ibkr_readonly_account,
                                 transport, account_id=account_id, paper_only=True)
        session = _profile_stage("SELECTED_ACCOUNT_READ", transport.get, "/iserver/accounts")
        _profile_selected_account(session, account_id)
        summary = _profile_stage("MARGIN_SUMMARY_READ", transport.get, f"/portfolio/{account_id}/summary")
        orders = _complete_profile_orders(transport, account_id)
        risk = _profile_stage("RISK_RECONCILIATION", paper_profile_preflight,
                              capture, summary, session, orders, now=datetime.now(timezone.utc))
        _profile_stage("HEARTBEAT", transport.keepalive)
        reasons = list(risk["reasons"])
        report = {**FLAGS, "status":"PAPER_PREFLIGHT_CHECK_COMPLETED",
                  "paper_identity_verified":True, "active_order_count":risk["active_order_count"],
                  "capture_age_seconds":risk["capture_age_seconds"],
                  "margin_monitor_state":risk["margin"]["monitor_state"],
                  "nlv_reconciliation_state":risk["margin"]["account_nlv_reconciliation"]["state"],
                  "account_trading_type_reported":risk["account_trading_type_reported"],
                  "block_reasons":reasons, "preview_requested":symbol is not None,
                  "whatif_attempted":False, "whatif_observed":False,
                  "financing_verified":False,"collateral_verified":False,
                  "order_specific_risk_approved":False,"submitted":False,
                  "trade_authorized":False,"ready_for_institutional_risk_gate":False}
        if symbol is None or reasons:
            return report
        if market_phase(datetime.now(timezone.utc).isoformat()) != "market":
            reasons.append("REGULAR_SESSION_REQUIRED")
            return report
        pool = MarketPool(transport, available_lines=1, dedicated_session=True, poll_interval=2)
        pool.connect()
        identity = resolve_us_equities(transport, [symbol])[symbol]
        ticket = _profile_ticket(account_id, identity, limit_price, shares)
        if Decimal(str(ticket["price"]))*shares > Decimal(str(risk["cash_bound_usd"])):
            reasons.append("PREVIEW_EXCEEDS_CASH_BOUND")
            return report
        pool.set_universe({identity["conid"]:1})
        _write_sync_json(PAPER_MARKET_RESERVATION, {"conids":[identity["conid"]],
                         "state":"ALLOCATING_OR_ACTIVE","created_at_utc":datetime.now(timezone.utc).isoformat()})
        try:
            pool.poll()
            snapshot = pool.poll()
            q = snapshot["quotes"][0]
            if not q["fresh_realtime_book_candidate"]:
                reasons.append("CURRENT_REALTIME_BOOK_REQUIRED")
                return report
            mark = Decimal(str(q["values"]["ask"]))
            price = Decimal(str(ticket["price"]))
            if abs(price/mark-1) > Decimal(".005"):
                reasons.append("PREVIEW_LIMIT_OUTSIDE_QUOTE_BAND")
                return report
            # Recheck selected account and all open orders immediately before
            # the preview; do not reuse the earlier order snapshot.
            from atlaspm.execution.ibkr_equity_snapshot import _orders
            selected = transport.get("/iserver/accounts")
            if (not isinstance(selected,dict) or selected.get("selectedAccount") != account_id
                    or account_id not in selected.get("accounts",[])
                    or ("isPaper" in selected and selected["isPaper"] is not True)):
                reasons.append("SELECTED_ACCOUNT_CHANGED")
                return report
            if _orders(_complete_profile_orders(transport, account_id), account_id)[0]:
                reasons.append("ACTIVE_ORDERS_REQUIRE_RECONCILIATION")
                return report
            transport.keepalive()
            now = datetime.now(timezone.utc)
            if not 0 <= now.timestamp()-q["provider_update_ms"]/1000 <= 15:
                reasons.append("QUOTE_EXPIRED_BEFORE_PREVIEW")
                return report
            from atlaspm.engine.contracts import timestamp
            if (now-timestamp(capture["captured_at_utc"],"captured_at")).total_seconds() > 120:
                reasons.append("PAPER_PREFLIGHT_CAPTURE_STALE")
                return report
            # Only this exact whatif route, no placement or reply endpoint.
            report["whatif_attempted"] = True
            class ExactPreview:
                def what_if(self, requested_account, requested_ticket):
                    if requested_account != account_id or requested_ticket != ticket:
                        raise ValueError("PREVIEW_TICKET_SCOPE_CHANGED")
                    return transport._request(f"/iserver/account/{account_id}/orders/whatif",
                                              preview={"orders":[ticket]})
            try:
                estimate = broker_estimate(ExactPreview(), account_id, ticket)
            except (ValueError, TypeError, KeyError, OSError) as exc:
                reasons.append(f"WHATIF_HTTP_{exc.status}" if isinstance(exc, GatewayHTTPError)
                               else "WHATIF_RESPONSE_UNVERIFIED")
                return report
            report["whatif_observed"] = True
            baseline = risk["margin"]["margin_summary_usd_claim"]
            if (abs(estimate["initial_margin"]["current"]-baseline["initial_margin"]) > 1
                    or abs(estimate["maintenance_margin"]["current"]-baseline["maintenance_margin"]) > 1):
                reasons.append("WHATIF_BASELINE_MARGIN_MISMATCH")
            if price*shares+Decimal(str(estimate["commission"])) > Decimal(str(risk["cash_bound_usd"])):
                reasons.append("WHATIF_CASH_PLUS_FEE_EXCEEDS_BOUND")
            # Preserve no-trade authority; a preview is not proof of future margin.
            report["status"] = "PAPER_WHATIF_DIAGNOSTIC_COMPLETED"
            return report
        finally:
            release = pool.close()
            if not release["released_all_owned"]:
                raise ValueError("MARKET_RELEASE_UNCERTAIN")
            marker.unlink()


def main() -> int:
    # Explicit separate mode: old reviewed cash-equity workflow unchanged.
    import sys
    if "--paper-profile-check" in sys.argv[1:]:
        parser = argparse.ArgumentParser(description="Read-only saved Paper profile risk and optional What-If diagnostic")
        parser.add_argument("--paper-profile-check",action="store_true",required=True)
        parser.add_argument("--symbol")
        parser.add_argument("--limit-price")
        parser.add_argument("--shares",type=int,default=1)
        parser.add_argument("--allow-paper-preview",action="store_true")
        args = parser.parse_args()
        try:
            result = profile_preview_check(symbol=args.symbol, limit_price=args.limit_price,
                                           shares=args.shares, allow_preview=args.allow_paper_preview)
            print(json.dumps(result,indent=2,allow_nan=False))
            return 0
        except ProfileCheckError as exc:
            print(json.dumps({"status":"PAPER_PREFLIGHT_BLOCKED","stage":exc.stage,
                              "reason":exc.reason, **exc.metadata,
                              "submitted":False,"trade_authorized":False,"live_execution_enabled":False}))
            return 2
        except (OSError,ValueError,TypeError,KeyError,OverflowError) as exc:
            from atlaspm.execution.ibkr_gateway import GatewayHTTPError
            reason = f"HTTP_{exc.status} at {exc.endpoint}" if isinstance(exc,GatewayHTTPError) else type(exc).__name__
            print(json.dumps({"status":"PAPER_PREFLIGHT_BLOCKED","reason":reason,
                              "submitted":False,"trade_authorized":False,"live_execution_enabled":False}))
            return 2
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
