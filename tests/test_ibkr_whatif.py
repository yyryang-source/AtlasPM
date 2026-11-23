from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from atlaspm.ibkr_whatif import PreviewTransport, preview_draft


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
        with patch("atlaspm.ibkr_whatif.time.sleep"):
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
