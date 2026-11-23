from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from atlaspm.ibkr_paper_orders import PaperTransport, cancel, main, reconcile, submit
from atlaspm.close_drafts import draft_after_close
from atlaspm.ibkr_next_day import next_day_review
from atlaspm.market_close import prepare_close
from atlaspm.paper_order_journal import PaperJournal
from atlaspm.quant.ibkr_gateway import GatewayHTTPError
from atlaspm.rebalance_workbench import Policy, RunStore, exchange_window


class PaperGateway:
    def __init__(self, account: str):
        self.account = account
        self.calls = []
        self.placement = [{"order_id": "151", "order_status": "Submitted"}]
        self.open_orders = []
        self.trades = []
        self.order_details = {}

    def get(self, endpoint):
        if endpoint == "/iserver/accounts":
            return {"accounts": [self.account], "isPaper": True, "selectedAccount": self.account}
        if endpoint == "/iserver/account/orders":
            return {"snapshot": True, "orders": self.open_orders}
        if endpoint == "/iserver/account/trades":
            return self.trades
        if endpoint.startswith("/iserver/account/order/status/"):
            self.calls.append(("status", endpoint))
            status = self.order_details.get(endpoint.rsplit("/", 1)[1], GatewayHTTPError(503))
            if isinstance(status, Exception):
                raise status
            return status
        raise AssertionError(endpoint)

    def place(self, account, ticket):
        self.calls.append(("place", account, ticket))
        if isinstance(self.placement, Exception):
            raise self.placement
        return self.placement

    def cancel(self, account, order_id):
        self.calls.append(("cancel", account, order_id))
        return {"msg": "Request was submitted", "order_id": order_id}


@unittest.skipUnless(importlib.util.find_spec("pandas_market_calendars"), "calendar extra required")
class PaperLifecycleTests(unittest.TestCase):
    def setUp(self):
        private = tempfile.TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.journal = PaperJournal(Path(private.name) / "paper.sqlite3")
        self.time = datetime(2026, 11, 23, 15, tzinfo=timezone.utc)
        self.account = "DU1234567"
        self.gateway = PaperGateway(self.account)
        self.ref = "ATL-" + "b" * 40
        self.account_hash = hashlib.sha256(self.account.encode()).hexdigest()
        self.baseline = {"positions": {"XYZ": 3}, "open_orders": [], "settled_cash": 10000,
                         "available_cash": 10000, "total_cash": 10000}
        self.original = {"id": "a" * 64, "as_of": "2026-11-20"}
        leg = {"symbol": "XYZ", "commission": 1.0, "initial_margin": {"change": 0.0},
               "maintenance_margin": {"change": 0.0}, "ticket": {
            "quantity": 5, "price": 100.0, "conid": 99, "orderType": "LMT",
            "side": "BUY", "tif": "DAY", "cOID": self.ref, "outsideRTH": False,
        }}
        self.fresh = {"status": "review", "parent_id": self.original["id"], "execution_session": "2026-11-23",
                      "account_id_sha256": self.account_hash, "checks": [{"passed": True}],
                      "broker_estimates": [leg], "next_day_snapshot": self.baseline,
                      "next_day_quotes": {"XYZ": {"updated_epoch": self.time.timestamp()}},
                      "observed_at": self.time.isoformat()}
        self.reviewed = deepcopy(self.fresh) | {"id": "c" * 64, "status": "reviewed"}
        self.snapshot = {"is_paper": True, "positions": {"XYZ": 3}, "total_cash": 10000,
                         "captured_at": self.time.isoformat()}

    def submit(self):
        with patch("atlaspm.ibkr_paper_orders.next_day_review", return_value=self.fresh):
            return submit(self.gateway, self.journal, self.reviewed, self.original, self.account, {}, now=self.time)

    def sync(self):
        with patch("atlaspm.ibkr_paper_orders.collect_cash_equity_snapshot", return_value=self.snapshot):
            return reconcile(self.gateway, self.journal, self.reviewed["id"], self.account)

    def order(self, filled, status):
        return {"acct": self.account, "orderId": 151, "order_ref": self.ref, "conid": 99,
                "side": "BUY", "status": status, "filledQuantity": filled, "remainingQuantity": 5 - filled}

    def fill(self, qty, fee):
        return {"account": self.account, "conid": 99, "size": qty, "side": "B",
                "price": "99.90", "commission": fee, "order_ref": self.ref, "execution_id": "execution-a",
                "trade_time_r": int(self.time.timestamp() * 1000)}

    def terminal_order(self):
        return {"order_id": 151, "order_status": "Cancelled", "account": self.account, "conid": 99,
                "symbol": "XYZ", "side": "B", "currency": "USD", "sec_type": "STK", "tif": "DAY",
                "order_type": "LIMIT", "total_size": "5.0", "size": "2.0", "cum_fill": "3.0",
                "order_time": self.time.strftime("%y%m%d%H%M%S")}

    def test_partial_fill_cancel_and_reconciliation_are_persisted(self):
        batch = self.submit()["batch"]
        self.assertEqual(batch["orders"][0]["state"], "working")
        self.assertEqual(self.gateway.calls[0][2], self.reviewed["broker_estimates"][0]["ticket"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.submit()
        self.gateway.open_orders = [self.order(3, "Submitted")]
        self.gateway.trades = [self.fill(3, "0.25")]
        self.snapshot.update(positions={"XYZ": 6}, total_cash=9700.05)
        self.assertEqual(self.sync()["status"], "pending")
        self.assertEqual(self.journal.get(batch["id"])["orders"][0]["state"], "partial")
        with patch("atlaspm.ibkr_paper_orders.collect_cash_equity_snapshot", return_value=self.snapshot):
            self.assertEqual(cancel(self.gateway, self.journal, batch["id"], self.account, self.ref)["status"],
                             "cancel_requested")
            self.assertEqual(self.sync()["status"], "pending")
            self.assertEqual(self.journal.get(batch["id"])["orders"][0]["state"], "cancel_requested")
            with self.assertRaisesRegex(ValueError, "No matching working broker order"):
                cancel(self.gateway, self.journal, batch["id"], self.account, self.ref)
        self.gateway.open_orders = [self.order(3, "Cancelled")]
        self.assertEqual(self.sync()["status"], "matched")
        self.assertEqual(self.sync()["status"], "matched")
        read_back = PaperJournal(self.journal.path).get(batch["id"])
        self.assertEqual(len(read_back["fills"]), 1)
        self.assertEqual(read_back["orders"][0]["state"], "cancelled")
        self.assertEqual(read_back["reconciliation"]["cash_expected"], 9700.05)
        self.assertEqual([item[0] for item in self.gateway.calls], ["place", "cancel"])

    def test_reconcile_watch_can_start_while_gateway_is_unavailable(self):
        run_id = self.submit()["batch"]["id"]
        arguments = ["paper", "reconcile", "--run", run_id, "--account", self.account, "--watch"]
        responses = [ValueError("Gateway temporarily unavailable"), {"status": "pending"},
                     {"status": "matched", "position_differences": {}}]
        output = io.StringIO()
        with (patch("sys.argv", arguments), redirect_stdout(output),
              patch("atlaspm.ibkr_paper_orders.PaperJournal", return_value=self.journal),
              patch("atlaspm.ibkr_paper_orders.PaperTransport", return_value=self.gateway),
              patch("atlaspm.ibkr_paper_orders.reconcile", side_effect=responses) as sync,
              patch("atlaspm.ibkr_paper_orders.time.sleep") as wait):
            self.assertEqual(main(), 0)
        self.assertEqual(sync.call_count, 3)
        self.assertEqual(wait.call_count, 2)
        self.assertEqual([json.loads(line)["status"] for line in output.getvalue().splitlines()],
                         ["waiting_for_broker", "pending", "matched"])

    def test_missing_live_order_can_recover_cached_cancellation_and_partial_execution(self):
        batch_id = self.submit()["batch"]["id"]
        self.gateway.order_details["151"] = self.terminal_order()
        self.gateway.trades = [self.fill(3, "0.25")]
        self.snapshot.update(positions={"XYZ": 6}, total_cash=9700.05)
        report = self.sync()
        self.assertEqual(report["status"], "matched")
        self.assertEqual(report["observed_working_client_ids"], [])
        self.assertEqual(self.journal.get(batch_id)["orders"][0]["state"], "cancelled")
        self.assertEqual([call[0] for call in self.gateway.calls], ["place", "status", "status"])

    def test_reused_or_unavailable_single_order_status_leaves_journal_pending(self):
        batch_id = self.submit()["batch"]["id"]
        self.gateway.order_details["151"] = self.terminal_order() | {"order_time": "261122150000"}
        with self.assertRaisesRegex(ValueError, "conflicts with the journaled Paper ticket"):
            self.sync()
        self.gateway.order_details.clear()
        self.assertEqual(self.sync()["status"], "pending")
        self.assertEqual(self.journal.get(batch_id)["orders"][0]["state"], "working")

    def test_cancel_requires_a_working_order_seen_in_the_current_snapshot(self):
        batch_id = self.submit()["batch"]["id"]
        self.gateway.open_orders = [self.order(0, "Submitted")]
        self.assertEqual(self.sync()["observed_working_client_ids"], [self.ref])
        self.gateway.open_orders.clear()
        with (patch("atlaspm.ibkr_paper_orders.collect_cash_equity_snapshot", return_value=self.snapshot),
              self.assertRaisesRegex(ValueError, "No matching working broker order")):
            cancel(self.gateway, self.journal, batch_id, self.account, self.ref)
        self.assertNotIn("cancel", [call[0] for call in self.gateway.calls])

    def test_timeout_recovers_from_trades_even_when_order_aged_out(self):
        self.gateway.placement = TimeoutError("lost broker connection")
        self.assertEqual(self.submit()["batch"]["orders"][0]["state"], "unknown")
        self.assertEqual(self.sync()["status"], "pending")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.submit()
        later_review = self.reviewed | {"id": "d" * 64, "execution_session": "2026-11-24"}
        later_draft = deepcopy(self.fresh)
        later_draft["broker_estimates"][0]["ticket"]["cOID"] = "ATL-" + "e" * 40
        with self.assertRaisesRegex(ValueError, "Resolve the ambiguous broker order"):
            self.journal.claim(later_review, later_draft)
        self.gateway.trades = [self.fill(5, None)]
        self.snapshot.update(positions={"XYZ": 8}, total_cash=9500.25)
        self.assertEqual(self.sync()["status"], "pending")
        self.gateway.trades[0]["commission"] = "0.25"
        self.assertEqual(self.sync()["status"], "matched")
        self.assertEqual(self.journal.get(self.reviewed["id"])["orders"][0]["state"], "filled")
        self.journal.claim(later_review, later_draft)
        self.assertEqual(len(self.gateway.calls), 1)

    def test_review_changes_and_official_reject_shape(self):
        self.fresh["broker_estimates"][0]["ticket"]["price"] = 102.0
        self.assertEqual(self.submit()["status"], "review_required")
        self.assertEqual(self.gateway.calls, [])
        self.fresh["broker_estimates"][0]["ticket"]["price"] = 100.0
        self.gateway.placement = {"error": "We cannot accept an order at the limit price you selected."}
        self.assertEqual(self.submit()["batch"]["orders"][0]["state"], "rejected")
        self.assertEqual(self.sync()["status"], "matched")

    def test_untraded_contract_change_requires_a_new_review(self):
        self.reviewed["next_day_contracts"] = {"XYZ": 99, "ABC": 1}
        self.fresh["next_day_contracts"] = {"XYZ": 99, "ABC": 2}
        self.assertEqual(self.submit()["status"], "review_required")
        self.assertEqual(self.gateway.calls, [])

    def test_approved_paper_transport_never_reaches_live_write(self):
        client = PaperTransport()
        live = {"accounts": [self.account], "selectedAccount": self.account, "isPaper": False}
        with patch.object(client, "get", return_value=live), patch.object(client, "_send_json") as network:
            with self.assertRaisesRegex(ValueError, "Paper session"):
                client.place(self.account, self.reviewed["broker_estimates"][0]["ticket"])
            with self.assertRaisesRegex(ValueError, "Paper session"):
                client.cancel(self.account, 151)
            network.assert_not_called()

    def test_id_reused_with_another_broker_reference_is_not_this_order(self):
        batch = self.submit()["batch"]
        self.gateway.open_orders = [self.order(0, "Submitted") | {"order_ref": "ANOTHER-STRATEGY"}]
        with self.assertRaisesRegex(ValueError, "identify different tickets"):
            self.sync()
        self.assertEqual(self.journal.get(batch["id"])["orders"][0]["state"], "working")

    def test_late_write_cannot_replace_a_newer_reconciliation(self):
        batch = self.submit()["batch"]
        newer = {"observed_at": "2026-11-23T15:03:00+00:00", "status": "matched"}
        older = {"observed_at": "2026-11-24T00:01:00+09:00", "status": "pending"}
        self.journal.save_reconciliation(batch["id"], newer)
        self.assertEqual(self.journal.save_reconciliation(batch["id"], older), newer)
        self.assertEqual(self.journal.get(batch["id"])["reconciliation"], newer)

    def test_paper_transport_sends_expected_http_paths_and_unchanged_ticket(self):
        client = PaperTransport()
        session = {"accounts": [self.account], "selectedAccount": self.account, "isPaper": True}
        ticket = self.reviewed["broker_estimates"][0]["ticket"]
        sent = []

        def response(request, *, timeout):
            endpoint = urlsplit(request.full_url)
            self.assertEqual(endpoint.scheme, "https")
            self.assertEqual(endpoint.hostname, "localhost")
            sent.append((request.method, endpoint.path, parse_qs(endpoint.query),
                         json.loads(request.data) if request.data else None))
            return io.BytesIO(json.dumps(session if endpoint.path.endswith("/accounts") else {}).encode())

        with patch.object(client.opener, "open", side_effect=response):
            client.stock_contracts(("XYZ", "ABC"))
            client.market_snapshot((99,))
            client.what_if(self.account, ticket)
            client.place(self.account, ticket)
            client.cancel(self.account, 151)
        account_path = f"/v1/api/iserver/account/{self.account}"
        self.assertEqual(sent, [
            ("GET", "/v1/api/trsrv/stocks", {"symbols": ["XYZ,ABC"]}, None),
            ("GET", "/v1/api/iserver/marketdata/snapshot", {"conids": ["99"], "fields": ["55,84,86,6509"]}, None),
            ("GET", "/v1/api/iserver/accounts", {}, None),
            ("POST", account_path + "/orders/whatif", {}, {"orders": [ticket]}),
            ("GET", "/v1/api/iserver/accounts", {}, None),
            ("POST", account_path + "/orders", {}, {"orders": [ticket]}),
            ("GET", "/v1/api/iserver/accounts", {}, None),
            ("DELETE", account_path + "/order/151", {}, None),
        ])

    def test_close_audit_to_paper_reconciliation_through_persisted_approvals(self):
        root = self.journal.path.parent
        directory = root / "market"
        day = date(2024, 11, 27)
        sessions, close = exchange_window(day)
        verified_at = close + timedelta(minutes=25)
        next_day = datetime(2024, 11, 29, 16, tzinfo=timezone.utc)
        manifest = root / "listings.csv"
        manifest.write_text("listing_symbol,provider_symbol,snapshot_utc\nXYZ,XYZ,2024-11-27T20:00:00Z\n",
                            encoding="utf-8")
        audit = prepare_close(
            directory, manifest, day=day, now=verified_at, symbols=("XYZ",), source="fixture",
            fetch=lambda *_: [(stamp, 65.0 + index * .1, 100.) for index, stamp in enumerate(sessions)],
        )
        store = RunStore(self.journal.path)

        class Gateway(PaperGateway):
            now = verified_at
            positions = 0
            cash = 10000

            def get(gateway, endpoint):
                account = gateway.account
                if endpoint == "/iserver/auth/status":
                    return {"authenticated": True, "connected": True}
                if endpoint == "/portfolio/accounts":
                    return [{"accountId": account, "tradingType": "CASH", "currency": "USD"}]
                if endpoint == f"/portfolio/{account}/ledger":
                    return {"USD": {"settledcash": 10000, "cashbalance": gateway.cash,
                                    "netliquidationvalue": 10000, "timestamp": gateway.now.timestamp()}}
                if endpoint == f"/portfolio2/{account}/positions":
                    return ([{"conid": 99, "position": gateway.positions, "currency": "USD",
                              "assetClass": "STK", "ticker": "XYZ"}] if gateway.positions else [])
                if endpoint == f"/iserver/account/{account}/summary":
                    return {"totalCashValue": gateway.cash, "availableFunds": gateway.cash}
                if endpoint == "/iserver/contract/99/info":
                    return {"con_id": 99, "instrument_type": "STK", "local_symbol": "XYZ", "currency": "USD"}
                return super().get(endpoint)

            def market_snapshot(gateway, conids):
                self.assertEqual(conids, (99,))
                return [{"conid": 99, "55": "XYZ", "84": "99.90", "86": "100.10",
                         "_updated": int(gateway.now.timestamp() * 1000), "6509": "R"}]

            def stock_contracts(gateway, symbols):
                self.assertEqual(symbols, ("XYZ",))
                return {"XYZ": [{"assetClass": "STK", "contracts": [{"conid": 99, "isUS": True}]}]}

            def what_if(gateway, account, ticket):
                margin = {"current": "0", "change": "0", "after": "0"}
                return {"amount": {"commission": "1 USD"}, "initial": margin, "maintenance": margin}

        gateway = Gateway(self.account)
        arguments = {"account_id": self.account, "policy": Policy(invested_fraction=.20), "now": verified_at,
                     "prepare_account_marks": lambda *_: self.fail("unexpected unsettled holdings")}
        close_record = draft_after_close(directory, audit, store, gateway, **arguments)
        self.assertEqual(close_record["status"], "review_available")
        close_id = close_record["run_id"]
        self.assertTrue(store.acknowledge(close_id, "Fixture close review"))
        original = store.get(close_id)
        gateway.now = next_day
        refreshed = next_day_review(gateway, original, self.account, {}, now=next_day)
        fresh_id = store.record_next_day(refreshed)
        self.assertTrue(store.acknowledge(fresh_id, "Fixture intraday review"))
        outcome = submit(gateway, self.journal, store.get(fresh_id), original, self.account, {}, now=next_day)
        ticket = outcome["batch"]["orders"][0]["ticket"]
        self.assertEqual(ticket["quantity"], 20)
        self.assertEqual(ticket["price"], 100.1)
        gateway.positions, gateway.cash = 20, 7998
        gateway.now = datetime.now(timezone.utc)
        gateway.open_orders = [self.order(5, "Filled") | {
            "order_ref": ticket["cOID"], "filledQuantity": 20, "remainingQuantity": 0,
        }]
        gateway.trades = [self.fill(20, "1.00") | {
            "order_ref": ticket["cOID"], "price": "100.05", "trade_time_r": int(next_day.timestamp() * 1000),
        }]
        self.assertEqual(reconcile(gateway, self.journal, fresh_id, self.account)["status"], "matched")
