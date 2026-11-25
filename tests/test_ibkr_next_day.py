from __future__ import annotations

import hashlib
import importlib.util
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from atlaspm.execution.ibkr_next_day import next_day_review
from atlaspm.rebalance_workbench import Policy


class NextDayGateway:
    def __init__(self, snapshot, timestamp):
        self.snapshot = snapshot
        self.timestamp = timestamp
        self.mark = 100.0
        self.commission = "1 USD"
        self.previewed = []
        self.reads = 0
        self.looked_up = []
        self.listings = [{"conid": 9, "exchange": "NASDAQ", "isUS": True},
                         {"conid": 55, "exchange": "MEXI", "isUS": False}]
        self.contract_currency = "USD"

    def get(self, endpoint):
        self.reads += 1
        if endpoint == "/iserver/accounts":
            account = self.snapshot["account_id"]
            return {"accounts": [account], "selectedAccount": account, "isPaper": True}
        return {"con_id": 9, "local_symbol": "XYZ", "instrument_type": "STK", "currency": self.contract_currency}

    def stock_contracts(self, symbols):
        self.looked_up.append(symbols)
        return {"XYZ": [{"assetClass": "STK", "contracts": self.listings}]}

    def market_snapshot(self, conids):
        assert conids == (9,)
        return [{"conid": 9, "55": "XYZ", "84": self.mark - .1, "86": self.mark + .1,
                 "6509": "R", "_updated": self.timestamp}]

    def what_if(self, account_id, ticket):
        self.previewed.append(ticket)
        no_margin = {"current": "0", "change": "0", "after": "0"}
        return {"amount": {"commission": self.commission}, "initial": no_margin, "maintenance": no_margin}


@unittest.skipUnless(importlib.util.find_spec("pandas_market_calendars"), "calendar extra required")
class NextDayTests(unittest.TestCase):
    def setUp(self):
        account = "DU0000000"
        self.now = datetime(2024, 11, 29, 16, tzinfo=timezone.utc)  # session after Thanksgiving
        self.snapshot = {
            "account_id": account, "is_paper": True, "currency": "USD", "account_type": "cash",
            "captured_at": self.now.isoformat(), "positions_and_orders_complete": True,
            "managed_equities_only": True, "settled_cash": 10000, "available_cash": 10000,
            "positions": {"XYZ": 3}, "open_orders": [], "contracts": {"XYZ": 9},
        }
        self.original = {
            "id": "a" * 64, "as_of": "2024-11-27", "status": "reviewed", "mode": "import",
            "account_source": "local_ibkr_cash_equity_snapshot", "scope_source": "fixture",
            "input_sha256": {"market_audit": "b" * 64},
            "policy": asdict(Policy()), "account_id_sha256": hashlib.sha256(account.encode()).hexdigest(),
            "draft": [{"symbol": "XYZ", "side": "BUY", "shares": 20, "reference_close": 100}],
            "target_weights": {"XYZ": .2},
        }
        self.transport = NextDayGateway(self.snapshot, int(self.now.timestamp() * 1000))

    def run_next_day(self, now=None, **kwargs):
        with patch("atlaspm.execution.ibkr_next_day.collect_cash_equity_snapshot", return_value=self.snapshot):
            return next_day_review(self.transport, self.original, self.snapshot["account_id"], {},
                                   now=now or self.now, **kwargs)

    def test_next_session_refresh_recalculates_qty_price_whatif_and_needs_review(self):
        self.transport.mark = 125
        result = self.run_next_day()
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["execution_session"], "2024-11-29")
        self.assertEqual(result["draft"][0]["shares"], 13)
        self.assertAlmostEqual(result["draft"][0]["limit_price"], 125.1)
        self.assertEqual(result["broker_estimates"][0]["ticket"], self.transport.previewed[0])
        self.assertEqual(result["draft_changes"][0]["previous_shares"], 20)
        self.assertEqual(result["parent_id"], self.original["id"])
        self.assertNotIn("account_id", result["next_day_snapshot"])
        self.assertEqual(result["account_confirmed_at"], self.now.isoformat())
        self.assertIn("next_day_account_confirmation", result["input_sha256"])

    def test_next_day_cash_and_broker_cost_fail_closed(self):
        self.snapshot["available_cash"] = 1750
        self.assertEqual(self.run_next_day()["status"], "review")
        self.transport.commission = "100 USD"
        result = self.run_next_day()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["draft"], [])
        self.assertIn("券商费用与资金", [row["name"] for row in result["checks"] if not row["passed"]])

    def test_next_day_discovers_new_us_stock_and_persists_the_resolved_contract(self):
        self.snapshot.update(positions={}, contracts={})
        result = self.run_next_day()
        self.assertEqual(self.transport.looked_up, [("XYZ",)])
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["next_day_contracts"], {"XYZ": 9})
        self.assertIn("next_day_contracts", result["input_sha256"])
        self.assertEqual(self.transport.previewed[0]["conid"], 9)

    def test_pending_order_during_whatif_blocks_the_next_day_draft(self):
        updated = self.snapshot | {"open_orders": [{"symbol": "XYZ", "side": "BUY", "remaining": 2}]}
        with patch("atlaspm.execution.ibkr_next_day.collect_cash_equity_snapshot", side_effect=[self.snapshot, updated]):
            result = next_day_review(self.transport, self.original, self.snapshot["account_id"], {}, now=self.now)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["draft"], [])
        self.assertEqual(len(result["broker_estimates"]), 1)
        self.assertTrue(any(not row["passed"] and row["detail"] == ["open_orders"] for row in result["checks"]))

    def test_quote_expiring_during_whatif_blocks_even_when_account_is_stable(self):
        finished = self.now + timedelta(seconds=31)
        confirmed = self.snapshot | {"captured_at": finished.isoformat()}
        quotes = {"XYZ": {"bid": 99.9, "ask": 100.1, "updated_epoch": self.now.timestamp()}}
        with (patch("atlaspm.execution.ibkr_next_day.collect_cash_equity_snapshot", side_effect=[self.snapshot, confirmed]),
              patch("atlaspm.execution.ibkr_next_day.live_quotes", return_value=quotes),
              patch("atlaspm.execution.ibkr_next_day.datetime", wraps=datetime) as clock):
            clock.now.side_effect = [self.now, self.now, finished]
            result = next_day_review(self.transport, self.original, self.snapshot["account_id"], {})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["draft"], [])
        self.assertTrue(any(not row["passed"] and row["detail"] == {"仍在交易时段": True, "报价过期": ["XYZ"]}
                            for row in result["checks"]))

    def test_ambiguous_or_non_usd_discovery_blocks_before_any_order_preview(self):
        self.snapshot.update(positions={}, contracts={})
        self.transport.listings.append({"conid": 10, "isUS": True})
        with self.assertRaisesRegex(ValueError, "mapping for XYZ"):
            self.run_next_day()
        self.transport.listings.pop()
        self.transport.contract_currency = "MXN"
        with self.assertRaisesRegex(ValueError, "contract validation failed for XYZ"):
            self.run_next_day()
        self.assertEqual(self.transport.previewed, [])

    def test_market_closed_and_wrong_account_do_not_read_gateway(self):
        with self.assertRaisesRegex(ValueError, "2024-11-29"):
            self.run_next_day(self.now + timedelta(hours=2))
        self.snapshot["account_id"] = "DU1111111"
        with self.assertRaisesRegex(ValueError, "belong to this account"):
            self.run_next_day()
        self.assertEqual(self.transport.reads, 0)

    def test_stale_account_prevents_broker_requests(self):
        self.snapshot["captured_at"] = (self.now - timedelta(minutes=3)).isoformat()
        with self.assertRaises(ValueError):
            self.run_next_day()
        self.assertEqual(self.transport.previewed, [])

    def test_reviewed_limit_retained_for_small_market_move_and_replaced_for_larger_move(self):
        limits = {("XYZ", "BUY"): Decimal("100.10")}
        self.transport.mark = 100.2
        self.assertEqual(self.run_next_day(reviewed_limits=limits)["draft"][0]["limit_price"], 100.1)
        self.transport.mark = 101.1
        self.assertAlmostEqual(self.run_next_day(reviewed_limits=limits)["draft"][0]["limit_price"], 101.2)
