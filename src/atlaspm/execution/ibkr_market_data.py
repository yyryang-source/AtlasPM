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
from threading import Lock
import time

from atlaspm.data.provider_policy import local_rights
from atlaspm.execution.ibkr_gateway import LocalGatewayTransport, DEFAULT_GATEWAY_URL

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
            except (ValueError, OSError):
                self._ready = False
                self._uncertain = True
                raise
            return {**_FLAGS, **self._status(), "provider":"ibkr", "model":"l1_snapshot_quality_v1",
                    "retrieved_at_utc":received.isoformat(),
                    "source_sha256":sha256(json.dumps(raw,sort_keys=True,allow_nan=False).encode()).hexdigest(),
                    "rights_status":"operator_attested_not_independently_verified",
                    "quotes":quotes, "alerts":[{"conid":r["conid"],"status":r["availability"]}
                         for r in quotes if not r["fresh_realtime_book_candidate"]]}

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


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--conid", type=int, action="append", required=True)
    parser.add_argument("--available-lines", type=int, required=True)
    parser.add_argument("--dedicated-session", action="store_true")
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--ca-file")
    parser.add_argument("--polls", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.polls <= 3:
        parser.error("Smoke test allows 1..3 polls")
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
