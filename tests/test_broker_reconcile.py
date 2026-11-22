"""Offline bank-export reconciliation must fail closed on account differences."""

import copy
import json
import unittest
from pathlib import Path

from atlaspm.broker_reconcile import reconcile_imported_broker_snapshot


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class BrokerExportReconcileTests(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads(
            (EXAMPLES / "institutional_batch_ledger.json").read_text(encoding="utf-8")
        )
        self.broker = json.loads(
            (EXAMPLES / "broker_export_synthetic.json").read_text(encoding="utf-8")
        )

    def reconcile(self):
        return reconcile_imported_broker_snapshot(self.ledger, self.broker)

    def test_matching_synthetic_import_does_not_enable_live_trading(self):
        r = self.reconcile()
        self.assertEqual(r["decision"], "MATCHED_UNVERIFIED_EXPORT")
        self.assertTrue(r["matched_account_and_positions"])
        self.assertFalse(r["source_authentication_verified"])
        self.assertFalse(r["broker_order_margin_preview_verified"])
        self.assertFalse(r["live_execution_enabled"])
        self.assertEqual(r["matched_position_count"], 2)
        self.assertEqual(r["block_reasons"], [])
        self.assertEqual(r["differences"], [])
        self.assertAlmostEqual(r["imported_balance_sheet_reconciliation_gap"], 0)

    def test_mismatched_stock_quantity_blocks(self):
        self.broker["positions"][0]["shares"] = 201
        self.broker["positions"][0]["market_value"] = 60300
        self.broker["net_liquidation"] = 124250
        r = self.reconcile()
        self.assertEqual(r["decision"], "BLOCK_RECONCILIATION")
        self.assertTrue(any(
            d["field"] == "equity:QQQ:shares" for d in r["differences"]
        ))
        self.assertIn("LOCAL_AND_IMPORTED_BROKER_VALUES_DIFFER", r["block_reasons"])

    def test_missing_treasury_and_extra_option_block(self):
        self.broker["positions"] = [
            self.broker["positions"][0],
            {"type": "option", "identifier": "FAKE-OPTION",
             "contracts": 1, "multiplier": 100, "market_value": 100},
        ]
        r = self.reconcile()
        self.assertIn("treasury:91282CDE0", r["missing_in_import"])
        self.assertIn("option:FAKE-OPTION", r["unexpected_in_import"])
        self.assertIn("BROKER_EXPORT_MISSING_LOCAL_POSITIONS", r["block_reasons"])
        self.assertIn("BROKER_EXPORT_HAS_UNKNOWN_POSITIONS", r["block_reasons"])

    def test_treasury_collateral_credit_mismatch_blocks_without_adding_cash(self):
        self.broker["broker_recognized_treasury_collateral_value"] = 95000
        result = self.reconcile()
        self.assertEqual(result["decision"], "BLOCK_RECONCILIATION")
        self.assertTrue(any(
            d["field"] == "broker_recognized_treasury_collateral_value"
            for d in result["differences"]
        ))
        self.assertAlmostEqual(result["local_net_liquidation"], 123950)

    def test_option_multiplier_and_contract_terms_comparison(self):
        self.broker["account_id"] = "DEMO-CASH"
        self.broker["account_type"] = "cash"
        self.broker.update({
            "settled_cash": 25000,
            "unsettled_receivable": 125,
            "unsettled_payable": 100,
            "margin_debt": 0,
            "accrued_financing_interest": 0,
            "net_liquidation": 45525,
            "available_funds": 25000,
            "excess_liquidity": 25000,
            "broker_recognized_treasury_collateral_value": 0,
            "positions": [
                {"type": "equity", "identifier": "XYZ",
                 "shares": 100, "market_value": 20000},
                {"type": "option", "identifier": "XYZ-2027-01-15-P-180",
                 "contracts": 1, "multiplier": 100,
                 "market_value": 500, "underlying": "XYZ",
                 "right": "put", "strike": 180,
                 "expiration_date": "2027-01-15"},
            ],
        })
        self.assertEqual(self.reconcile()["decision"], "MATCHED_UNVERIFIED_EXPORT")
        self.broker["positions"][1]["multiplier"] = 50
        self.broker["positions"][1]["strike"] = 190
        result = self.reconcile()
        self.assertEqual(result["decision"], "BLOCK_RECONCILIATION")
        fields = [d["field"] for d in result["differences"]]
        self.assertIn("option:XYZ-2027-01-15-P-180:multiplier", fields)
        self.assertIn("option:XYZ-2027-01-15-P-180:strike", fields)

    def test_broker_cash_and_debt_diverge(self):
        self.broker["settled_cash"] += 500
        self.broker["margin_debt"] += 500
        r = self.reconcile()
        self.assertEqual(r["decision"], "BLOCK_RECONCILIATION")
        self.assertTrue(any(
            d["field"] == "settled_cash" for d in r["differences"]
        ))
        self.assertTrue(any(
            d["field"] == "margin_debt" for d in r["differences"]
        ))

    def test_broker_export_internal_balance_sheet_gap_blocks(self):
        self.broker["net_liquidation"] = 130000
        r = self.reconcile()
        self.assertIn(
            "IMPORTED_BROKER_EXPORT_BALANCE_SHEET_DOES_NOT_RECONCILE",
            r["block_reasons"]
        )

    def test_stale_snapshot_blocks(self):
        self.broker["captured_at"] = "2026-11-20T20:45:00Z"
        r = self.reconcile()
        self.assertIn("IMPORTED_BROKER_EXPORT_STALE", r["block_reasons"])

    def test_negative_broker_excess_liquidity_blocks(self):
        self.broker["excess_liquidity"] = -100
        r = self.reconcile()
        self.assertIn("IMPORTED_BROKER_LIQUIDITY_NEGATIVE", r["block_reasons"])

    def test_local_unreconciled_short_option_blocks(self):
        self.ledger["accounts"][1]["positions"].append({
            "type": "option", "identifier": "QQQ-SHORT-CALL",
            "underlying": "QQQ", "right": "call", "strike": 350,
            "expiration_date": "2027-01-15",
            "contracts": -1, "multiplier": 100, "mark_premium": 4,
            "mark_source": "synthetic", "mark_age_seconds": 3
        })
        self.ledger["accounts"][1]["broker_reported_net_liquidation"] -= 400
        r = self.reconcile()
        self.assertIn("LOCAL_LEDGER_NOT_READY_FOR_RECONCILIATION", r["block_reasons"])

    def test_wrong_account_or_currency_rejected(self):
        self.broker["account_id"] = "NOT-IN-LEDGER"
        with self.assertRaisesRegex(ValueError, "select exactly one"):
            self.reconcile()
        self.broker["account_id"] = "DEMO-MARGIN"
        self.broker["currency"] = "EUR"
        with self.assertRaisesRegex(ValueError, "USD"):
            self.reconcile()

    def test_duplicate_broker_positions_rejected(self):
        self.broker["positions"].append(copy.deepcopy(self.broker["positions"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate position"):
            self.reconcile()

    def test_future_broker_capture_and_bad_source_rejected(self):
        self.broker["captured_at"] = "2026-11-20T22:00:00Z"
        with self.assertRaisesRegex(ValueError, "later than ledger"):
            self.reconcile()
        self.broker["captured_at"] = "2026-11-20T20:59:45Z"
        self.broker["source"] = "web_scraping"
        with self.assertRaisesRegex(ValueError, "imported_broker_export"):
            self.reconcile()

    def test_fractional_option_quantity_rejected(self):
        self.broker["positions"].append({
            "type": "option", "identifier": "FAULT",
            "contracts": 0.5, "multiplier": 100, "market_value": 10,
        })
        with self.assertRaisesRegex(ValueError, "nonzero integers"):
            self.reconcile()


if __name__ == "__main__":
    unittest.main()
