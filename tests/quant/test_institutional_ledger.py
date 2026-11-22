"""Offline multi-asset cash/margin ledger and reconciliation regression tests."""

import copy
import json
import unittest
from pathlib import Path

from atlaspm.institutional_ledger import build_institutional_ledger


SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "institutional_ledger.json"


class InstitutionalLedgerTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(SAMPLE.read_text(encoding="utf-8"))

    def test_cash_and_margin_nlv_reconcile_without_cross_netting(self):
        report = build_institutional_ledger(self.payload)
        cash, margin = report["accounts"]
        self.assertEqual(len(report["accounts"]), 2)
        self.assertEqual(cash["account_type"], "cash")
        self.assertEqual(margin["account_type"], "margin")
        self.assertAlmostEqual(cash["net_liquidation_calculated"], 45525)
        self.assertAlmostEqual(margin["net_liquidation_calculated"], 117150)
        self.assertTrue(cash["is_reconciled"])
        self.assertTrue(margin["is_reconciled"])
        self.assertAlmostEqual(report["consolidated"]["net_liquidation"], 162675)
        self.assertTrue(report["consolidated"]["no_cross_account_collateral_netting"])
        self.assertEqual(report["consolidated"]["accounts_ready_for_risk_review"], 1)
        self.assertIn("SHORT_OPTIONS_REQUIRE_BROKER_MARGIN_VERIFICATION", margin["alerts"])

    def test_treasury_dirty_price_dv01_not_double_counted(self):
        report = build_institutional_ledger(self.payload)
        margin = report["accounts"][1]
        treasury = next(p for p in margin["positions"] if p["type"] == "treasury")
        self.assertAlmostEqual(treasury["clean_market_value"], 97500)
        self.assertAlmostEqual(treasury["accrued_interest_in_dirty_value"], 400)
        self.assertAlmostEqual(treasury["market_value"], 97900)
        self.assertAlmostEqual(treasury["dv01_usd_per_bp"], 42.5865)
        self.assertAlmostEqual(treasury["broker_recognized_collateral_value"], 90000)
        self.assertFalse(treasury["collateral_credit_is_cash"])
        self.assertAlmostEqual(report["consolidated"]["broker_recognized_treasury_collateral_report_only"], 90000)
        self.assertAlmostEqual(report["consolidated"]["settled_cash"], 35000)

    def test_long_put_value_and_short_call_liability(self):
        cash, margin = build_institutional_ledger(self.payload)["accounts"]
        put = next(p for p in cash["positions"] if p["type"] == "option")
        call = next(p for p in margin["positions"] if p["type"] == "option")
        self.assertAlmostEqual(put["market_value"], 500)
        self.assertAlmostEqual(call["market_value"], -800)
        self.assertAlmostEqual(call["short_option_liability"], 800)
        self.assertTrue(call["requires_broker_option_margin_validation"])

    def test_cash_account_rejects_negative_settled_cash(self):
        case = copy.deepcopy(self.payload)
        case["accounts"][0]["settled_cash"] = -1
        with self.assertRaisesRegex(ValueError, "negative settled cash"):
            build_institutional_ledger(case)

    def test_cash_account_cannot_have_margin_debt(self):
        case = copy.deepcopy(self.payload)
        case["accounts"][0]["margin_debt"] = 1
        with self.assertRaisesRegex(ValueError, "Cash accounts cannot"):
            build_institutional_ledger(case)

    def test_cash_account_cannot_have_short_option(self):
        case = copy.deepcopy(self.payload)
        case["accounts"][0]["positions"][1]["contracts"] = -1
        with self.assertRaisesRegex(ValueError, "Cash account cannot include short"):
            build_institutional_ledger(case)

    def test_missing_broker_nlv_blocks_ready_state(self):
        case = copy.deepcopy(self.payload)
        case["accounts"][0].pop("broker_reported_net_liquidation")
        report = build_institutional_ledger(case)
        cash = report["accounts"][0]
        self.assertFalse(cash["is_reconciled"])
        self.assertIn("BROKER_NET_LIQUIDATION_NOT_RECONCILED", cash["alerts"])

    def test_broker_nlv_difference_triggers_reconciliation_alert(self):
        case = copy.deepcopy(self.payload)
        case["accounts"][0]["broker_reported_net_liquidation"] += 1000
        cash = build_institutional_ledger(case)["accounts"][0]
        self.assertAlmostEqual(cash["reconciliation_difference"], -1000)
        self.assertFalse(cash["account_ledger_ready_for_risk_review"])

    def test_stale_market_price_blocks_ready(self):
        case = copy.deepcopy(self.payload)
        case["accounts"][0]["positions"][0]["mark_age_seconds"] = 400
        cash = build_institutional_ledger(case)["accounts"][0]
        self.assertEqual(cash["stale_instrument_identifiers"], ["XYZ"])
        self.assertIn("INSTRUMENT_MARK_STALE", cash["alerts"])

    def test_stale_account_snapshot_blocks_ready(self):
        case = copy.deepcopy(self.payload)
        case["accounts"][0]["snapshot_at"] = "2026-11-20T20:40:00Z"
        cash = build_institutional_ledger(case)["accounts"][0]
        self.assertIn("BROKER_ACCOUNT_SNAPSHOT_STALE", cash["alerts"])

    def test_unknown_treasury_has_zero_collateral_but_still_asset_value(self):
        case = copy.deepcopy(self.payload)
        treasury = case["accounts"][1]["positions"][1]
        treasury["collateral"] = {"status": "UNKNOWN"}
        report = build_institutional_ledger(case)
        margin = report["accounts"][1]
        self.assertAlmostEqual(margin["net_liquidation_calculated"], 117150)
        self.assertAlmostEqual(margin["broker_recognized_treasury_collateral_report_only"], 0)
        self.assertIn("TREASURY_COLLATERAL_UNVERIFIED_OR_INELIGIBLE", margin["alerts"])

    def test_fake_or_overstated_treasury_credit_rejected(self):
        for change in ({"source": "self"}, {"broker_recognized_value": 1000000}):
            case = copy.deepcopy(self.payload)
            case["accounts"][1]["positions"][1]["collateral"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_institutional_ledger(case)

    def test_duplicate_accounts_or_instruments_rejected(self):
        case = copy.deepcopy(self.payload)
        case["accounts"][1]["account_id"] = "DEMO-CASH"
        with self.assertRaisesRegex(ValueError, "Duplicate account_id"):
            build_institutional_ledger(case)
        case = copy.deepcopy(self.payload)
        case["accounts"][0]["positions"].append(
            copy.deepcopy(case["accounts"][0]["positions"][0])
        )
        with self.assertRaisesRegex(ValueError, "Duplicate instrument"):
            build_institutional_ledger(case)

    def test_unsupported_currency_or_naive_date_rejected(self):
        case = copy.deepcopy(self.payload)
        case["accounts"][0]["currency"] = "EUR"
        with self.assertRaisesRegex(ValueError, "USD"):
            build_institutional_ledger(case)
        case = copy.deepcopy(self.payload)
        case["valuation_at"] = "2026-11-20T21:00:00"
        with self.assertRaisesRegex(ValueError, "timezone"):
            build_institutional_ledger(case)

    def test_invalid_option_contract_and_price_rejected(self):
        case = copy.deepcopy(self.payload)
        case["accounts"][1]["positions"][2]["contracts"] = 1.5
        with self.assertRaisesRegex(ValueError, "signed integer"):
            build_institutional_ledger(case)
        case = copy.deepcopy(self.payload)
        case["accounts"][1]["positions"][1]["clean_price_per_100"] = -2
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            build_institutional_ledger(case)

    def test_future_snapshot_rejected(self):
        case = copy.deepcopy(self.payload)
        case["accounts"][0]["snapshot_at"] = "2026-11-21T01:00:00Z"
        with self.assertRaisesRegex(ValueError, "future"):
            build_institutional_ledger(case)


if __name__ == "__main__":
    unittest.main()
