"""Bounded IBKR Client Portal L1 polling using the existing local transport.

Single owner of reserved data lines in a dedicated session. No order client,
paid regulatory snapshots, auto login, competing session takeover or
unsubscribe-all. Polling snapshots are not tick-by-tick trades or depth.
"""
from __future__ import annotations

from argparse import ArgumentParser
from datetime import UTC, datetime
from hashlib import sha256
import json
from math import isfinite
import re
import sys
from pathlib import Path
from threading import Lock
import time

from atlaspm.data.provider_policy import local_rights
from atlaspm.execution.ibkr_gateway import (
    LocalGatewayTransport, DEFAULT_GATEWAY_URL, load_paper_profile,
    collect_ibkr_readonly_account, _paper_sync_lock, _paper_sso_cooldown,
    _write_sync_json, _sync_path, _PAPER_SSO_ATTEMPT,
)

FIELDS = {"31":"last", "84":"bid", "86":"ask", "85":"ask_size", "88":"bid_size",
          "7059":"last_size", "7762":"volume", "7308":"delta", "7309":"gamma",
          "7310":"theta", "7311":"vega", "7633":"iv_percent", "7638":"open_interest",
          "7698":"last_yield_percent", "7699":"bid_yield_percent", "7720":"ask_yield_percent"}
REQUEST_FIELDS = tuple(FIELDS) + ("6509",)
_FLAGS = {"research_only":True, "approved_for_model_training":False,
          "approved_for_backtest":False, "live_execution_enabled":False,
          "broker_order_submission_enabled":False, "external_ai_allowed":False,
          "point_in_time_verified":False}


def _number(value, *, signed=False):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", value):
        # Do not silently strip delayed/frozen flags, size suffixes or currency units.
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    if not isfinite(number) or (not signed and number < 0):
        return None
    return number


def _conid(value):
    if type(value) is not int or not 1 <= value <= 2**63-1:
        raise ValueError("Expected verified positive conid")
    return value


def resolve_us_equities(transport, symbols):
    """Resolve unique US stock/ETF listing then verify its contract identity."""
    local_rights("ibkr")
    if (not isinstance(symbols, (list, tuple)) or not 1 <= len(symbols) <= 20
            or any(not isinstance(s, str) or not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", s)
                   for s in symbols) or len(set(symbols)) != len(symbols)):
        raise ValueError("Invalid bounded US equity symbols")
    lookup = transport.market_stock_lookup(symbols)
    if not isinstance(lookup, dict) or set(lookup) != set(symbols):
        raise ValueError("Symbol lookup response mismatch")
    result = {}
    for symbol in symbols:
        entries = lookup[symbol]
        if not isinstance(entries, list) or len(entries) > 100:
            raise ValueError("Invalid stock lookup entries")
        candidates = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid stock lookup entry")
            if entry.get("assetClass") != "STK":
                continue
            contracts = entry.get("contracts")
            if not isinstance(contracts, list) or len(contracts) > 100:
                raise ValueError("Invalid stock lookup contracts")
            for contract in contracts:
                if not isinstance(contract, dict) or type(contract.get("isUS")) is not bool:
                    raise ValueError("Stock listing country unverified")
                if contract["isUS"]:
                    candidates.add(_conid(contract.get("conid")))
        if len(candidates) != 1:
            raise ValueError("US equity contract missing or ambiguous: " + symbol)
        conid = next(iter(candidates))
        info = transport.market_contract_info(conid)
        if not isinstance(info, dict):
            raise ValueError("Contract information missing")
        for aliases, expected in (
            (("con_id", "conid"), conid), (("symbol", "ticker"), symbol),
            (("instrument_type", "secType"), "STK"), (("currency",), "USD"),
        ):
            values = [info[k] for k in aliases if k in info]
            if not values or any(type(v) is not type(expected) or v != expected for v in values):
                raise ValueError("Contract identity mismatch: " + symbol)
        if conid in (row["conid"] for row in result.values()):
            raise ValueError("Duplicate contract across symbols")
        result[symbol] = {"symbol": symbol, "conid": conid, "currency": "USD",
                          "asset_class": "STK", "broker_identity_checked": True,
                          "security_master_identity_verified": False}
    return result


def normalize_snapshot(raw, wanted, *, received_at, max_age_seconds=15):
    if not isinstance(raw, list) or len(raw) > len(wanted):
        raise ValueError("Unexpected snapshot row count")
    rows = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Invalid snapshot row")
        conid = _conid(item.get("conid"))
        if conid not in wanted or conid in rows:
            raise ValueError("Unrequested or duplicate snapshot contract")
        availability = item.get("6509")
        status = {"R":"REALTIME_REPORTED", "D":"DELAYED", "Z":"FROZEN",
                  "Y":"FROZEN_DELAYED", "N":"NOT_SUBSCRIBED"}.get(
                      availability[:1] if isinstance(availability, str) else "", "UNKNOWN")
        updated = item.get("_updated")
        age = None
        if type(updated) is int and 0 < updated <= 10**14:
            age = received_at.timestamp() - updated/1000
        values = {name: _number(item.get(tag), signed=(name in {"delta","gamma","theta","vega"}
                                                       or "yield" in name))
                  for tag, name in FIELDS.items()}
        bid, ask = values["bid"], values["ask"]
        valid_book = bid is not None and ask is not None and 0 < bid <= ask
        fresh = (status == "REALTIME_REPORTED" and age is not None and
                 0 <= age <= max_age_seconds and valid_book)
        rows[conid] = {"conid": conid, "availability": status,
                       "provider_update_ms": updated if age is not None else None,
                       "age_seconds": age, "fresh_realtime_book_candidate": fresh,
                       "values": values, "midpoint": (bid+ask)/2 if fresh else None,
                       "spread": ask-bid if fresh else None,
                       "field_level_timestamp_verified": False,
                       "directional_signal": None}
    return [rows.get(c, {"conid": c, "availability":"MISSING", "age_seconds":None,
                          "fresh_realtime_book_candidate":False, "values":{},
                          "midpoint":None, "spread":None,
                          "directional_signal":None}) for c in wanted]


class MarketPool:
    """Process-local line budget; any ambiguous broker call freezes new lines.

    Capacity must be the confirmed *available* lines after other consumers.
    Each requested contract must be uniquely mapped before admission.
    Reconnect is operator-driven: revalidate session and retry only reserved
    IDs, do not assume old subscriptions vanished. No automatic login.
    """
    def __init__(self, transport, *, available_lines, dedicated_session=False,
                 max_age_seconds=15, poll_interval=1.0, clock=time.monotonic, sleep=time.sleep):
        local_rights("ibkr")
        if dedicated_session is not True:
            raise ValueError("Explicit dedicated market data session ownership required")
        if type(available_lines) is not int or not 1 <= available_lines <= 100:
            raise ValueError("Confirm available market data lines (local cap 100)")
        if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 300:
            raise ValueError("Invalid freshness budget")
        if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int,float)) or not 1 <= poll_interval <= 60:
            raise ValueError("Invalid poll interval")
        self.transport = transport
        self.capacity = available_lines
        self.max_age = max_age_seconds
        self.interval = float(poll_interval)
        self.clock, self.sleep = clock, sleep
        self.lock = Lock()
        self._reserved = set()
        self._desired = ()
        self._waiting = ()
        self._ready = False
        self._uncertain = False
        self._next_poll = 0.0

    def connect(self):
        """Validate existing session, not a new authentication flow."""
        local_rights("ibkr")
        with self.lock:
            self._ready = False
            auth = self.transport.get("/iserver/auth/status")
            if (not isinstance(auth, dict) or auth.get("authenticated") is not True
                    or auth.get("connected") is not True or auth.get("competing") is True):
                raise ValueError("IBKR session is not ready; operator login/recovery required")
            # Required Web API market-data initialization. Do not return account IDs.
            accounts = self.transport.get("/iserver/accounts")
            if not isinstance(accounts, dict) or not isinstance(accounts.get("accounts"), list) or not accounts["accounts"]:
                raise ValueError("IBKR brokerage account initialization failed")
            self._ready = True
            return {"session_ready":True, "reserved_lines":len(self._reserved),
                    "uncertain_subscriptions":self._uncertain, **_FLAGS}

    def set_universe(self, priorities):
        """Mapping conid -> integer priority (larger first); deterministic ties."""
        if not isinstance(priorities, dict) or len(priorities) > 1000:
            raise ValueError("Candidate universe must be bounded to 1000")
        for c,p in priorities.items():
            _conid(c)
            if type(p) is not int or not 0 <= p <= 1000000:
                raise ValueError("Invalid subscription priority")
        ordered = tuple(sorted(priorities, key=lambda c:(-priorities[c],c)))
        with self.lock:
            self._desired = ordered[:self.capacity]
            self._waiting = ordered[self.capacity:]
            return self._status()

    def _status(self):
        return {"selected":list(self._desired), "waiting_count":len(self._waiting),
                "reserved_lines":len(self._reserved), "line_capacity":self.capacity,
                "session_ready":self._ready, "uncertain_subscriptions":self._uncertain}

    def _release(self, conid):
        answer = self.transport.market_unsubscribe(conid)
        if not isinstance(answer, dict) or answer.get("success") is not True:
            raise ValueError("IBKR stream release unconfirmed")
        self._reserved.remove(conid)

    def poll(self):
        local_rights("ibkr")
        with self.lock:
            if not self._ready:
                raise ValueError("Connect existing brokerage session before polling")
            if self._uncertain:
                raise ValueError("Subscription state uncertain; release owned IDs before adding lines")
            try:
                for c in sorted(self._reserved-set(self._desired)):
                    self._release(c)
                if not self._desired:
                    return {**_FLAGS, **self._status(), "quotes":[]}
                delay = self._next_poll-self.clock()
                if delay > 0:
                    self.sleep(delay)
                # Reserve before I/O because timeout could still allocate broker lines.
                self._reserved.update(self._desired)
                self._next_poll = self.clock()+self.interval
                raw = self.transport.market_snapshot(self._desired, REQUEST_FIELDS)
                received = datetime.now(UTC)
                quotes = normalize_snapshot(raw, self._desired, received_at=received,
                                            max_age_seconds=self.max_age)
                digest=sha256(json.dumps(raw,sort_keys=True,allow_nan=False).encode()).hexdigest()
            except (ValueError, OSError):
                self._ready = False
                self._uncertain = True
                raise
            return {**_FLAGS, **self._status(), "provider":"ibkr", "model":"l1_snapshot_quality_v1",
                    "retrieved_at_utc":received.isoformat(),
                    "source_sha256":digest,
                    "rights_status":"operator_attested_not_independently_verified",
                    "quotes":quotes, "alerts":[{"conid":r["conid"],"status":r["availability"]}
                         for r in quotes if not r["fresh_realtime_book_candidate"]]}

    def history(self, conid, *, period="1d", bar="5min"):
        """Return validated raw provider OHLCV, without guessing timestamp/volume units."""
        local_rights("ibkr")
        _conid(conid)
        with self.lock:
            if not self._ready or self._uncertain or conid not in self._desired:
                raise ValueError("History requires ready session and selected contract")
            try:
                raw = self.transport.market_history(conid, period, bar)
                if not isinstance(raw, dict) or not isinstance(raw.get("data"), list) or len(raw["data"]) > 1000:
                    raise ValueError("Invalid historical response")
                expected_seconds = {"1min":60,"5min":300,"15min":900,"1h":3600,"1d":86400}.get(bar)
                if expected_seconds is None or raw.get("barLength") != expected_seconds:
                    raise ValueError("Historical bar interval mismatch")
                previous = None
                for row in raw["data"]:
                    if not isinstance(row, dict) or type(row.get("t")) is not int or row["t"] <= 0:
                        raise ValueError("Invalid provider history timestamp")
                    if previous is not None and row["t"] <= previous:
                        raise ValueError("Unordered or duplicate historical bar")
                    previous = row["t"]
                    vals = [_number(row.get(k)) for k in ("o","h","l","c","v")]
                    if any(v is None for v in vals):
                        raise ValueError("Invalid historical OHLCV")
                    op,hi,lo,cl,_ = vals
                    if not 0 < lo <= min(op,cl) <= max(op,cl) <= hi:
                        raise ValueError("Invalid historical price geometry")
                digest=sha256(json.dumps(raw,sort_keys=True,allow_nan=False).encode()).hexdigest()
            except (ValueError,OSError):
                self._ready=False
                self._uncertain=True
                raise
            return {**_FLAGS,"provider":"ibkr","conid":conid,"model":"history_raw_qc_v1",
                    "retrieved_at_utc":datetime.now(UTC).isoformat(),"source_sha256":digest,
                    "period":period,"bar":bar,"provider_metadata":{k:raw.get(k) for k in
                    ("symbol","barLength","mdAvailability","mktDataDelay","volumeFactor","outsideRth")},
                    "rows":raw["data"],"timestamp_and_volume_units_verified":False,
                    "coverage_complete":False,"rights_status":"operator_attested_not_independently_verified"}

    def close(self):
        """Release only owned/reserved IDs; no automatic unsubscribe-all.

        If a release fails, retain the reservation and refuse new allocations.
        Operator must revalidate session and reconcile uncertain subscriptions.
        """
        with self.lock:
            failures = []
            for c in sorted(self._reserved):
                try:
                    self._release(c)
                except (ValueError, OSError):
                    failures.append(c)
            self._uncertain = bool(failures)
            self._ready = False
            return {"released_all_owned":not failures, "remaining_lines":len(self._reserved),
                    "unconfirmed_release_count":len(failures), **_FLAGS}


PAPER_MARKET_RESERVATION = Path("local_data/ibkr_paper_market_reservation.json")


def run_symbol_probe(symbols, *, available_lines, dedicated_session, polls=3,
                     include_history=False, output=None):
    """Short, local Paper-only probe. No data retention without separate rights."""
    local_rights("ibkr", retention=output is not None)
    if type(polls) is not int or not 2 <= polls <= 3:
        raise ValueError("Symbol probe requires 2..3 polls (first is preflight)")
    if (type(available_lines) is not int or not 1 <= available_lines <= 100
            or not isinstance(symbols, (list, tuple)) or not 1 <= len(symbols) <= min(5, available_lines)
            or dedicated_session is not True):
        raise ValueError("Confirm dedicated session and available lines; symbol probe cap 5")
    if type(include_history) is not bool:
        raise ValueError("Invalid history flag")
    if output is not None:
        # Fixed output avoids overwriting the profile, risk snapshot or journal.
        output = _sync_path(Path("local_data/ibkr_paper_market_probe.json"))
    with _paper_sync_lock():
        marker = _sync_path(PAPER_MARKET_RESERVATION)
        if marker.exists():
            raise ValueError("Prior market reservations uncertain; operator reconciliation required")
        profile = load_paper_profile()
        transport = LocalGatewayTransport(profile["url"], ca_file=profile["ca_file"])
        def progress(message):
            print("[IBKR Market] " + message, file=sys.stderr, flush=True)
        _paper_sso_cooldown(progress)
        _write_sync_json(_PAPER_SSO_ATTEMPT, {"attempted_at_utc": datetime.now(UTC).isoformat()})
        # Reuse independent SSO + DEMO checks. No order or margin authority.
        capture = collect_ibkr_readonly_account(transport, account_id=profile["account"], paper_only=True)
        if capture.get("paper_identity_verified") is not True:
            raise ValueError("Paper market session identity not verified")
        del capture
        pool = MarketPool(transport, available_lines=available_lines,
                          dedicated_session=True, poll_interval=2)
        pool.connect()
        identities = resolve_us_equities(transport, symbols)
        ids = {row["conid"]: 1 for row in identities.values()}
        pool.set_universe(ids)
        # Persist intent before any allocation; a crash/failed release must not
        # lead to new reservations on restart. Never unsubscribe-all.
        _write_sync_json(PAPER_MARKET_RESERVATION, {"conids": sorted(ids),
                         "state": "ALLOCATING_OR_ACTIVE", "created_at_utc": datetime.now(UTC).isoformat()})
        reports = []
        histories = {}
        try:
            for index in range(polls):
                progress(f"Snapshot {index + 1}/{polls}")
                reports.append(pool.poll())
            if include_history:
                for symbol, identity in identities.items():
                    progress("History: " + symbol)
                    histories[symbol] = pool.history(identity["conid"])
            transport.keepalive()
        finally:
            release = pool.close()
            if not release["released_all_owned"]:
                raise ValueError("Market stream release uncertain; keep reservation marker for operator review")
            marker.unlink()
        last = reports[-1]
        by_id = {row["conid"]: row for row in last["quotes"]}
        result = {**_FLAGS, "status": "PAPER_MARKET_PROBE_COMPLETED",
                  "paper_identity_verified": True, "release_confirmed": True,
                  "rights_status": "operator_attested_not_independently_verified",
                  "polls_completed": len(reports), "quote_count": len(by_id),
                  "symbols": {symbol: {**identity,
                      "availability": by_id[identity["conid"]]["availability"],
                      "fresh_realtime_book_candidate": by_id[identity["conid"]]["fresh_realtime_book_candidate"],
                      "history_row_count": len(histories.get(symbol, {}).get("rows", []))}
                      for symbol, identity in identities.items()},
                  "fresh_realtime_book_candidates": sum(q["fresh_realtime_book_candidate"] for q in by_id.values()),
                  "history_requested": include_history,
                  "history_units_verified": False}
        if output is not None:
            _write_sync_json(Path("local_data/ibkr_paper_market_probe.json"), {
                **result, "latest_snapshot": last, "history": histories,
                "captured_at_utc": datetime.now(UTC).isoformat(),
            })
            result["written"] = "local_data/ibkr_paper_market_probe.json"
        return result


def main():
    parser = ArgumentParser(description=__doc__)
    instruments = parser.add_mutually_exclusive_group(required=True)
    instruments.add_argument("--conid", type=int, action="append")
    instruments.add_argument("--symbol", action="append", help="US equity symbol; uses saved Paper profile")
    parser.add_argument("--history", action="store_true", help="Symbol probe: request 1d/5min history")
    parser.add_argument("--save-local", action="store_true", help="Symbol probe: requires separate local retention rights")
    parser.add_argument("--available-lines", type=int, required=True)
    parser.add_argument("--dedicated-session", action="store_true")
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--ca-file")
    parser.add_argument("--polls", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.polls <= 3:
        parser.error("Smoke test allows 1..3 polls")
    if args.symbol:
        if args.ca_file or args.gateway_url != DEFAULT_GATEWAY_URL:
            parser.error("Symbol mode uses the saved Paper profile, not URL/CA overrides")
        try:
            result = run_symbol_probe(args.symbol, available_lines=args.available_lines,
                                      dedicated_session=args.dedicated_session, polls=args.polls,
                                      include_history=args.history,
                                      output=True if args.save_local else None)
            print(json.dumps(result, indent=2))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # Market rights and safe identity failures are actionable. Do not
            # print raw broker payloads or private local account paths.
            from atlaspm.execution.ibkr_gateway import GatewayHTTPError
            if isinstance(exc, GatewayHTTPError):
                parser.exit(1, f"IBKR market probe failed: HTTP_{exc.status} at {exc.endpoint}\n")
            if isinstance(exc, ValueError) and re.fullmatch(r"[A-Za-z0-9 _.:;/-]{1,150}", str(exc)):
                parser.exit(1, "IBKR market probe failed: " + str(exc) + "\n")
            parser.exit(1, "IBKR market probe failed; verify local profile and session\n")
        return
    if args.history or args.save_local:
        parser.error("--history and --save-local require --symbol")
    local_rights("ibkr")
    transport = LocalGatewayTransport(args.gateway_url, ca_file=args.ca_file)
    pool = MarketPool(transport, available_lines=args.available_lines,
                      dedicated_session=args.dedicated_session)
    pool.set_universe({c:1 for c in args.conid})
    try:
        pool.connect()
        for _ in range(args.polls):
            report = pool.poll()
            quotes = report.pop("quotes")
            report.pop("selected", None)
            report.pop("alerts", None)
            report["quote_count"] = len(quotes)
            report["fresh_realtime_book_candidates"] = sum(q["fresh_realtime_book_candidate"] for q in quotes)
            print(json.dumps(report, indent=2))
    finally:
        result = pool.close()
        print(json.dumps(result, indent=2))
        if not result["released_all_owned"]:
            raise ValueError("Unconfirmed market stream release; operator review required")


if __name__ == "__main__":
    main()
