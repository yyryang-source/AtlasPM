"""Offline tests for read-only add/reduce plans and the RMS gate."""

import copy
import json
import unittest
from pathlib import Path

from atlaspm.execution.institutional_rebalance import plan_equity_trade


ROOT = Path(__file__).resolve().parents[2] / "examples"


class InstitutionalRebalanceTests(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads(
            (ROOT / "institutional_ledger.json").read_text(encoding="utf-8")
        )
        self.plan = json.loads(
            (ROOT / "institutional_equity_plan.json").read_text(encoding="utf-8")
        )

    def _margin_candidate(self, *, borrowing_allowed: bool):
        # Remove the *unverified* short option to demonstrate a clean margin
        # account. This changes the ledger NLV by +800.
        self.ledger["accounts"][1]["positions"].pop()
        self.ledger["accounts"][1]["broker_reported_net_liquidation"] = 117950
        self.plan["account_id"] = "DEMO-MARGIN"
        self.plan["trade"] = {
            "side": "BUY", "symbol": "QQQ", "shares": 40,
            "limit_price": 300, "estimated_fee": 2,
            "client_order_id": "SYN-QQQ-002",
            "allow_margin_borrowing": borrowing_allowed,
        }
        self.plan["broker_snapshot"].update({
            "account_id": "DEMO-MARGIN", "net_liquidation": 117950,
            "margin_debt": 50000, "available_funds": 60000,
            "excess_liquidity": 48000,
        })
        self.plan["broker_preview"].update({
            "account_id": "DEMO-MARGIN", "client_order_id": "SYN-QQQ-002",
            "symbol": "QQQ", "side": "BUY", "shares": 40,
            "limit_price": 300, "available_funds_after": 56000,
            "excess_liquidity_after": 44000,
            "margin_debt_after": 52302,
        })
        self.plan["risk_observation"].update({
            "pro_forma_gross_leverage": 1.5,
            "pro_forma_stress_loss_fraction": .26,
        })
        self.plan["policy"]["max_single_order_fraction"] = .15

    def test_cash_buy_proforma_and_paper_only_allow(self):
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertEqual(result["decision"], "ALLOW_PAPER_INTENT")
        self.assertTrue(result["eligible_for_paper_intent"])
        self.assertFalse(result["live_execution_enabled"])
        self.assertEqual(result["before"]["shares"], 100)
        after = result["pro_forma_after_unexecuted_trade"]
        self.assertEqual(after["shares"], 110)
        self.assertAlmostEqual(after["settled_cash"], 22998)
        self.assertAlmostEqual(after["margin_debt"], 0)
        self.assertAlmostEqual(after["net_liquidation_at_original_marks"], 45523)
        self.assertEqual(result["block_reasons"], [])

    def test_cash_sell_lowers_position_and_credits_cash(self):
        self.plan["trade"].update({
            "side": "SELL", "shares": 5, "estimated_fee": 1,
        })
        self.plan["broker_preview"].update({
            "side": "SELL", "shares": 5,
            "available_funds_after": 25999,
            "excess_liquidity_after": 25999,
        })
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertEqual(result["decision"], "ALLOW_PAPER_INTENT")
        after = result["pro_forma_after_unexecuted_trade"]
        self.assertEqual(after["shares"], 95)
        self.assertAlmostEqual(after["settled_cash"], 25999)
        self.assertAlmostEqual(after["net_liquidation_at_original_marks"], 45524)

    def test_cash_purchase_cannot_spend_reserved_obligations(self):
        self.plan["trade"]["shares"] = 125
        self.plan["broker_preview"]["shares"] = 125
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertEqual(result["decision"], "BLOCK")
        self.assertIn("CASH_ACCOUNT_INSUFFICIENT_SETTLED_CASH", result["block_reasons"])
        self.assertEqual(result["pro_forma_after_unexecuted_trade"]["settled_cash"], 100)

    def test_unsettled_payables_above_settled_cash_block_trade(self):
        # Regression for the early blocker path: reserve obligations and
        # append the blocker after the list has been initialized.
        self.ledger["accounts"][0]["settled_cash"] = 50
        self.ledger["accounts"][0]["broker_reported_net_liquidation"] = 20575
        self.plan["broker_snapshot"]["net_liquidation"] = 20575
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertEqual(result["decision"], "BLOCK")
        self.assertIn(
            "SETTLED_CASH_BELOW_UNSETTLED_PAYABLE", result["block_reasons"]
        )

    def test_old_mark_blocks_add(self):
        self.ledger["accounts"][0]["positions"][0]["mark_age_seconds"] = 300
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertEqual(result["decision"], "BLOCK")
        self.assertIn("TRADED_INSTRUMENT_MARK_STALE", result["block_reasons"])

    def test_stale_or_mismatched_broker_snapshot_blocks(self):
        self.plan["broker_snapshot"]["net_liquidation"] = 55525
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertIn("BROKER_SNAPSHOT_NLV_MISMATCH", result["block_reasons"])
        self.plan["broker_snapshot"]["net_liquidation"] = 45525
        self.plan["broker_snapshot"]["data_age_seconds"] = 900
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertIn("BROKER_SNAPSHOT_STALE", result["block_reasons"])

    def test_preview_must_match_intent_and_account(self):
        self.plan["broker_preview"]["client_order_id"] = "REUSED-FROM-OTHER-TRADE"
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertIn("BROKER_PREVIEW_NOT_BOUND_TO_TRADE", result["block_reasons"])
        self.plan["broker_preview"]["client_order_id"] = "SYN-XYZ-BUY-001"
        self.plan["broker_preview"]["account_id"] = "WRONG"
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertIn("BROKER_PREVIEW_NOT_BOUND_TO_TRADE", result["block_reasons"])

    def test_missing_preview_fails_closed(self):
        self.plan.pop("broker_preview")
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertEqual(result["decision"], "BLOCK")
        self.assertIn("BROKER_WHATIF_PREVIEW_MISSING", result["block_reasons"])

    def test_price_or_supplied_risk_understatement_blocks(self):
        self.plan["trade"]["limit_price"] = 215
        self.plan["broker_preview"]["limit_price"] = 215
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertIn("LIMIT_PRICE_TOO_FAR_FROM_ACCOUNT_MARK", result["block_reasons"])
        self.plan["trade"]["limit_price"] = 200
        self.plan["broker_preview"]["limit_price"] = 200
        self.plan["risk_observation"]["pro_forma_gross_leverage"] = .1
        self.plan["risk_observation"]["pro_forma_stress_loss_fraction"] = .01
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertIn("PROFORMA_LEVERAGE_UNDERREPORTED", result["block_reasons"])
        self.assertIn("PROFORMA_STRESS_UNDERREPORTED", result["block_reasons"])

    def test_margin_account_short_options_block_new_order(self):
        # Original example margin account has short calls requiring independent
        # broker options margin review; account NLV may still reconcile.
        self.plan["account_id"] = "DEMO-MARGIN"
        self.plan["trade"].update({
            "symbol": "QQQ", "shares": 1, "limit_price": 300,
        })
        self.plan["broker_snapshot"].update({
            "account_id": "DEMO-MARGIN",
            "net_liquidation": 117150,
            "margin_debt": 50000,
        })
        self.plan["broker_preview"].update({
            "account_id": "DEMO-MARGIN",
            "symbol": "QQQ", "shares": 1,
            "limit_price": 300,
            "margin_debt_after": 50000,
        })
        self.plan["risk_observation"].update({
            "pro_forma_gross_leverage": 1.5,
            "pro_forma_stress_loss_fraction": .26,
        })
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertEqual(result["decision"], "BLOCK")
        self.assertIn("LEDGER_NOT_READY_FOR_RISK_REVIEW", result["block_reasons"])

    def test_margin_borrowing_requires_explicit_optin(self):
        self._margin_candidate(borrowing_allowed=False)
        result = plan_equity_trade(self.ledger, self.plan)
        self.assertIn("MARGIN_BORROWING_NOT_EXPLICITLY_AUTHORIZED", result["block_reasons"])
        self.assertEqual(result["decision"], "BLOCK")

    def test_margin_borrowing_requires_current_preview_and_cap(self):
        self._margin_candidate(borrowing_allowed=True)
        allowed = plan_equity_trade(self.ledger, self.plan)
        self.assertEqual(allowed["decision"], "ALLOW_PAPER_INTENT")
        after = allowed["pro_forma_after_unexecuted_trade"]
        self.assertAlmostEqual(after["settled_cash"], 300)
        self.assertAlmostEqual(after["margin_debt"], 52302)
        self.assertAlmostEqual(after["incremental_margin_borrowing"], 2302)
        self.assertAlmostEqual(after["net_liquidation_at_original_marks"], 117948)
        self.plan["broker_preview"]["margin_debt_after"] = 50000
        blocked = plan_equity_trade(self.ledger, self.plan)
        self.assertIn("BROKER_PREVIEW_DEBT_MISMATCH", blocked["block_reasons"])

    def test_normalized_account_without_exact_raw_match_raises_value_error(self):
        # The ledger strips account IDs; the separate raw account lookup must
        # fail with the documented validation exception, never StopIteration.
        self.ledger["accounts"][0]["account_id"] = " DEMO-CASH "
        with self.assertRaisesRegex(ValueError, "raw ledger account"):
            plan_equity_trade(self.ledger, self.plan)

    def test_sell_above_holdings_is_rejected(self):
        self.plan["trade"]["side"] = "SELL"
        self.plan["trade"]["shares"] = 200
        with self.assertRaisesRegex(ValueError, "exceeds existing"):
            plan_equity_trade(self.ledger, self.plan)

    def test_buy_new_unverified_security_is_rejected(self):
        self.plan["trade"]["symbol"] = "NOT-HELD"
        with self.assertRaisesRegex(ValueError, "already-held"):
            plan_equity_trade(self.ledger, self.plan)


if __name__ == "__main__":
    unittest.main()
