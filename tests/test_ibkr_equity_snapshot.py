from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime, timezone

from atlaspm.ibkr_equity_snapshot import collect_cash_equity_snapshot
from atlaspm.execution.ibkr_gateway import FixtureGatewayTransport


class CashEquitySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.account = "DU1234567"
        self.now = datetime(2026, 11, 20, 21, 30, tzinfo=timezone.utc)
        self.order = {
            "acct": self.account, "tier": "fixture", "orderId": 114, "ticker": "XYZ", "conid": 100,
            "cashCcy": "USD", "secType": "STK", "status": "Submitted", "side": "BUY",
            "remainingQuantity": 5.0, "filledQuantity": 7.0, "totalSize": 12.0, "orderType": "Limit", "price": "154.02",
        }
        self.responses = {
            "/iserver/accounts": {"accounts": [self.account], "selectedAccount": self.account,
                                 "isPaper": True, "sessionId": "fixture-session"},
            "/iserver/auth/status": {"authenticated": True, "connected": True},
            "/iserver/account/orders": {"orders": [self.order], "snapshot": True},
            "/portfolio/accounts": [{"accountId": self.account, "tradingType": "CASH", "currency": "USD"}],
            f"/portfolio/{self.account}/ledger": {"USD": {"currency": "USD", "settledcash": 20000,
                                                        "cashbalance": 20500, "netliquidationvalue": 23000,
                                                        "timestamp": int(self.now.timestamp() * 1000)}},
            f"/portfolio2/{self.account}/positions": [
                {"currency": "USD", "assetClass": "STK", "ticker": "XYZ", "position": 19.0, "conid": 100}
            ],
            f"/iserver/account/{self.account}/summary": {"availableFunds": 19000},
        }

    def capture(self, transport=None):
        return collect_cash_equity_snapshot(
            transport or FixtureGatewayTransport({"responses": self.responses}),
            account_id=self.account, dedicated=True, now=self.now,
        )

    def test_partial_fill_uses_broker_position_and_remaining_order(self):
        snapshot = self.capture()
        self.assertEqual(snapshot["positions"], {"XYZ": 19})
        self.assertEqual(snapshot["open_orders"][0]["remaining"], 5)
        self.assertEqual(snapshot["available_cash"], 19000.0)
        self.assertEqual(snapshot["contracts"], {"XYZ": 100})
        self.assertTrue(snapshot["is_paper"])

    def test_selected_paper_account_requires_broker_paper_confirmation(self):
        transport = FixtureGatewayTransport({"responses": self.responses})

        def capture():
            return collect_cash_equity_snapshot(transport, account_id=None, dedicated=True, now=self.now)

        self.assertEqual(capture()["account_id"], self.account)
        transport.called.clear()
        transport.responses["/iserver/accounts"]["isPaper"] = False
        with self.assertRaisesRegex(ValueError, "authenticated IBKR Paper session"):
            capture()
        self.assertEqual(transport.called, ["/iserver/accounts"])

    def test_working_order_and_position_contract_conflict_is_blocked(self):
        self.order["conid"] = 200
        with self.assertRaisesRegex(ValueError, "Conflicting IBKR"):
            self.capture()

    def test_changed_fills_during_capture_block_the_snapshot(self):
        class MovingOrders(FixtureGatewayTransport):
            def get(self, endpoint):
                out = super().get(endpoint)
                if endpoint == "/iserver/account/orders" and self.called.count(endpoint) == 2:
                    out = deepcopy(out)
                    out["orders"][0].update(remainingQuantity=4.0, filledQuantity=8.0)
                return out

        with self.assertRaisesRegex(ValueError, "changed during snapshot"):
            self.capture(MovingOrders({"responses": self.responses}))

    def test_wrong_account_and_stale_ledgers_are_rejected(self):
        self.responses["/iserver/accounts"]["selectedAccount"] = "DU7654321"
        with self.assertRaisesRegex(ValueError, "Select the intended"):
            self.capture()
        self.responses["/iserver/accounts"]["selectedAccount"] = self.account
        self.responses[f"/portfolio/{self.account}/ledger"]["USD"]["timestamp"] = int(self.now.timestamp()) - 360
        with self.assertRaisesRegex(ValueError, "timestamp is stale"):
            self.capture()

    def test_non_cash_account_and_incomplete_orders_are_rejected(self):
        self.responses["/portfolio/accounts"][0]["tradingType"] = "PMRGN"
        with self.assertRaisesRegex(ValueError, "CASH"):
            self.capture()
        self.responses["/portfolio/accounts"][0]["tradingType"] = "CASH"
        self.responses["/iserver/account/orders"]["snapshot"] = False
        with self.assertRaisesRegex(ValueError, "complete live-order"):
            self.capture()
