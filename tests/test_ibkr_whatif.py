from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from atlaspm.execution.ibkr_whatif import PreviewTransport, preview_draft


class FakePreview:
    def __init__(self, now):
        self.account = "DU1234567"
        self.is_paper = True
        self.requests = []
        self.bid = "99.99"
        self.ask = "100.01"
        self.updated = int(now.timestamp() * 1000)
        self.feed = "R"
        self.commission = "1 USD"
        self.warning = None

    def get(self, path):
        if path == "/iserver/accounts":
            return {"accounts": [self.account], "selectedAccount": self.account, "isPaper": self.is_paper}
        if path == "/iserver/contract/101/info":
            return {"con_id": 101, "local_symbol": "XYZ", "instrument_type": "STK", "currency": "USD"}
        raise ValueError("Unexpected endpoint")

    def market_snapshot(self, conids):
        assert conids == (101,)
        return [{"conid": 101, "55": "XYZ", "84": self.bid, "86": self.ask, "6509": self.feed, "_updated": self.updated}]

    def what_if(self, account, ticket):
        self.requests.append((account, ticket))
        return {
            "amount": {"commission": self.commission}, "initial": {"current": "0", "change": "1", "after": "1"},
            "maintenance": {"current": "0", "change": "1", "after": "1"}, "warn": self.warning, "error": None,
        }


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
        self.gateway = FakePreview(self.now)
        self.snapshot = {
            "account_id": self.gateway.account, "is_paper": True, "positions": {"XYZ": 2},
            "contracts": {"XYZ": 101}, "settled_cash": 2500., "available_cash": 2000., "open_orders": [],
        }
        self.run = {
            "id": "b" * 64, "as_of": "2026-11-20", "status": "reviewed", "input_sha256": {"market_audit": "a" * 64},
            "account_id_sha256": hashlib.sha256(self.gateway.account.encode()).hexdigest(),
            "draft": [{"symbol": "XYZ", "side": "BUY", "shares": 5, "reference_close": 100.0}],
            "positions": [{"symbol": "XYZ", "held": 2, "pending_buy": 0, "pending_sell": 0}],
        }

    def preview(self):
        return preview_draft(self.gateway, self.run, self.snapshot, {}, now=self.now)

    def test_exact_ticket_commission_and_margin_are_persistable(self):
        report = self.preview()
        self.assertEqual(report["status"], "previewed")
        leg = report["estimates"][0]
        self.assertEqual(leg["ticket"], self.gateway.requests[0][1])
        self.assertEqual(leg["ticket"]["quantity"], 5)
        self.assertEqual(leg["ticket"]["price"], 100.0)
        self.assertEqual(report["cash_required"], 501.0)
        self.assertEqual(leg["maintenance_margin"]["after"], 1.0)
        self.assertFalse(report["submitted"])

    def test_paper_verification_before_quotes_or_preview(self):
        self.gateway.is_paper = False
        with self.assertRaisesRegex(ValueError, "authenticated IBKR Paper session"):
            self.preview()
        self.assertEqual(self.gateway.requests, [])

    def test_warning_or_broker_fees_above_available_cash_block(self):
        self.gateway.warning = "market data unavailable"
        self.assertEqual(self.preview()["status"], "blocked")
        self.gateway.warning = None
        self.snapshot["available_cash"] = 500.50
        self.assertIn("FRESH_CASH", self.preview()["reasons"][0])

    def test_position_or_mark_changes_prevent_previewing_old_ticket(self):
        self.snapshot["positions"]["XYZ"] += 1
        self.assertIn("ACCOUNT_CHANGED", self.preview()["reasons"][0])
        self.snapshot["positions"]["XYZ"] -= 1
        self.gateway.ask = "102.00"
        self.assertIn("FRESH_QUOTE", self.preview()["reasons"][0])
        self.assertEqual(self.gateway.requests, [])

    def test_delayed_or_stale_feed_blocks(self):
        self.gateway.feed = "D"
        with patch("atlaspm.execution.ibkr_whatif.time.sleep"):
            with self.assertRaisesRegex(ValueError, "Missing current real-time"):
                self.preview()
            self.gateway.feed = "R"
            self.gateway.updated -= 60_000
            with self.assertRaisesRegex(ValueError, "Missing current real-time"):
                self.preview()
        self.assertEqual(self.gateway.requests, [])

    def test_transport_blocks_other_post_paths_before_network(self):
        client = PreviewTransport()
        with self.assertRaisesRegex(ValueError, "only allows POST"):
            client._request(f"/iserver/account/{self.gateway.account}/orders", preview={"orders": []})


class ProfilePreflightTests(unittest.TestCase):
    def fixture(self):
        now = datetime.now(timezone.utc)
        capture = {"account_id":"DUR123","captured_at_utc":now.isoformat(),
                   "source":"local_authenticated_ibkr_gateway","paper_identity_verified":True,
                   "gateway_tls_certificate_validated":True,"session_reported_authenticated":True,
                   "live_execution_enabled":False,"broker_order_submission_enabled":False,
                   "raw":{"selected_account":{"accountId":"DUR123","type":"DEMO",
                          "currency":"USD","brokerageAccess":True,"tradingType":"STKNOPT"},
                          "USD_ledger":{"USD":{"settledcash":1000}}}}
        session = {"selectedAccount":"DUR123","accounts":["DUR123"]}
        orders = {"snapshot":True,"orders":[]}
        margin = {"margin_summary_usd_claim":{"total_nlv":1002,"available_funds":1000,"excess_liquidity":1000},
                  "monitor_state":"BLOCKED_DATA","data_integrity_block_reasons":["FOREIGN_NLV_UNEXPLAINED"],
                  "risk_alerts":[]}
        return now,capture,session,orders,margin

    def test_dur_preflight_preserves_foreign_block_and_unknown_financing(self):
        from atlaspm.execution.ibkr_whatif import paper_profile_preflight
        now,capture,session,orders,margin = self.fixture()
        with patch("atlaspm.risk.ibkr_margin_monitor.normalize_ibkr_margin_summary",return_value=margin):
            out = paper_profile_preflight(capture,{},session,orders,now=now)
        self.assertEqual(out["reasons"],["FOREIGN_NLV_UNEXPLAINED"])
        self.assertEqual(out["cash_bound_usd"],1000)
        self.assertFalse(out["financing_verified"])
        self.assertFalse(out["collateral_verified"])

    def test_preflight_rejects_wrong_account_or_incomplete_order_snapshot(self):
        from atlaspm.execution.ibkr_whatif import paper_profile_preflight
        now,capture,session,orders,margin = self.fixture()
        with self.assertRaises(ValueError):
            paper_profile_preflight(capture,{},dict(session,isPaper=False),orders,now=now)
        with self.assertRaises(ValueError):
            paper_profile_preflight(capture,{},session,{"orders":[]},now=now)
        with self.assertRaises(ValueError):
            paper_profile_preflight(dict(capture,paper_identity_verified=False),{},session,orders,now=now)

    def test_preview_ticket_bounded_and_explicit(self):
        from atlaspm.execution.ibkr_whatif import _profile_ticket, profile_preview_check
        identity={"conid":10,"currency":"USD","asset_class":"STK","broker_identity_checked":True}
        ticket=_profile_ticket("DUR123",identity,"100.00",1)
        self.assertEqual(ticket["side"],"BUY")
        self.assertTrue(ticket["cOID"].startswith("ATL-WHATIF-"))
        for price, shares in (("100",11),("2501",1),("100.001",1),("NaN",1)):
            with self.assertRaises(ValueError):
                _profile_ticket("DUR123",identity,price,shares)
        with self.assertRaises(ValueError):
            profile_preview_check(symbol="AAA",limit_price="100",allow_preview=False)


class OrderSnapshotDiagnosticTests(unittest.TestCase):
    def test_initial_blank_response_is_not_used_as_snapshot(self):
        from atlaspm.execution.ibkr_whatif import _complete_profile_orders
        from unittest.mock import Mock
        transport = Mock()
        transport._request.return_value = []
        complete = {"snapshot":True,"orders":[]}
        transport.get.return_value = complete
        self.assertIs(_complete_profile_orders(transport,"DUR123"),complete)
        transport._request.assert_called_once_with("/iserver/account/orders",params={"force":"true"})
        transport.get.assert_called_once_with("/iserver/account/orders")

    def test_incomplete_second_response_fails_closed_with_redacted_shape(self):
        from atlaspm.execution.ibkr_whatif import _complete_profile_orders, ProfileCheckError
        from unittest.mock import Mock
        transport = Mock()
        transport._request.return_value = []
        for response in ([], {"orders":[]}, {"snapshot":False,"orders":[]},
                         {"snapshot":True,"orders":[],"error":"private DUR123"}):
            transport.get.return_value = response
            with self.assertRaises(ProfileCheckError) as ctx:
                _complete_profile_orders(transport,"DUR123")
            self.assertEqual(ctx.exception.stage,"ORDER_LIST_VALIDATE")
            self.assertEqual(ctx.exception.reason,"COMPLETE_ORDER_SNAPSHOT_REQUIRED")
            self.assertNotIn("DUR123",str(ctx.exception.metadata))

    def test_selected_account_error_redacts_values_and_stage(self):
        from atlaspm.execution.ibkr_whatif import _profile_selected_account, ProfileCheckError, _profile_stage
        with self.assertRaises(ProfileCheckError) as ctx:
            _profile_selected_account({"selectedAccount":"U123","accounts":["U123"]},"DUR123")
        self.assertEqual(ctx.exception.stage,"SELECTED_ACCOUNT")
        self.assertFalse(ctx.exception.metadata["selected_matches"])
        self.assertNotIn("U123",str(ctx.exception.metadata))
        def fail():
            raise ValueError("private account DUR123")
        with self.assertRaises(ProfileCheckError) as ctx:
            _profile_stage("MARGIN_SUMMARY_READ",fail)
        self.assertEqual(ctx.exception.reason,"ValueError")
