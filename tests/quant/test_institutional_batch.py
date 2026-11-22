"""Multi-asset basket accounting and fail-closed preflight regression tests.

No real brokerage requests. All examples are deliberately fictional.
"""

import copy
import json
import unittest
from pathlib import Path

from atlaspm.institutional_batch import propose_multi_asset_batch


EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


class InstitutionalBatchTests(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads(
            (EXAMPLES / "institutional_batch_ledger.json").read_text(encoding="utf-8")
        )
        self.plan = json.loads(
            (EXAMPLES / "institutional_batch_plan.json").read_text(encoding="utf-8")
        )

    def test_combined_basket_accounting_and_rms(self):
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertEqual(report["decision"], "ALLOW_PAPER_INTENT")
        self.assertEqual(report["block_reasons"], [])
        self.assertFalse(report["live_execution_enabled"])
        self.assertFalse(report["broker_preview_is_authenticated"])
        self.assertEqual(len(report["leg_risk_verdicts"]), 4)
        after = report["pro_forma_unexecuted"]
        self.assertAlmostEqual(after["net_liquidation"], 123945)
        self.assertAlmostEqual(after["settled_cash"], 3706)
        self.assertAlmostEqual(after["unsettled_receivable"], 3499)
        self.assertAlmostEqual(after["margin_debt"], 48000)
        self.assertAlmostEqual(after["total_fee"], 5)
        self.assertAlmostEqual(after["broker_recognized_treasury_credit_report_only"], 90000)
        self.assertTrue(after["self_financing_identity_verified"])
        positions = {(p["type"], p["identifier"]): p for p in after["positions"]}
        self.assertEqual(positions["equity", "QQQ"]["shares"], 190)
        self.assertEqual(positions["treasury", "91282CDE0"]["face_value"], 110000)
        self.assertAlmostEqual(positions["treasury", "91282CDE0"]["market_value"], 107690)
        self.assertEqual(positions["option", "QQQ-2027-01-15-P-280"]["market_value"], 500)
        self.assertEqual(len(positions), 3)

    def test_put_contracts_must_not_exceed_remaining_stock(self):
        self.plan["legs"][2]["contracts"] = 3
        self.plan["basket_preview"]["order_legs"] = copy.deepcopy(self.plan["legs"])
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertEqual(report["decision"], "BLOCK")
        self.assertIn("PROTECTIVE_PUT_EXCEEDS_UNDERLYING_SHARES", report["block_reasons"])

    def test_independent_reconciliation_when_treasury_limit_differs_from_mark(self):
        # A purchase at 97.80 against a 97.90 dirty mark adds $10 of
        # hypothetical marked gain, minus $5 total transaction costs.
        self.plan["legs"][1]["dirty_price_per_100"] = 97.8
        self.plan["basket_preview"]["order_legs"] = copy.deepcopy(self.plan["legs"])
        self.plan["basket_preview"]["net_liquidation_after"] = 123955
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertEqual(report["decision"], "ALLOW_PAPER_INTENT")
        self.assertAlmostEqual(
            report["pro_forma_unexecuted"]["net_liquidation"], 123955
        )
        self.assertAlmostEqual(
            report["pro_forma_unexecuted"][
                "expected_net_liquidation_change_from_fees_and_price_deviation"
            ], 5
        )
        self.assertAlmostEqual(
            report["pro_forma_unexecuted"]["net_liquidation_change_reconciliation_gap"],
            0, places=7,
        )

    def test_equity_add_and_treasury_put_repayment_share_same_cash_budget(self):
        self.plan["legs"][0]["action"] = "BUY_EQUITY"
        self.plan["basket_preview"]["order_legs"] = copy.deepcopy(self.plan["legs"])
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertEqual(report["decision"], "ALLOW_PAPER_INTENT")
        after = report["pro_forma_unexecuted"]
        self.assertAlmostEqual(after["settled_cash"], 705)
        self.assertAlmostEqual(after["unsettled_receivable"], 500)
        self.assertAlmostEqual(after["net_liquidation"], 123945)
        qqq = next(p for p in after["positions"] if p["type"] == "equity")
        self.assertAlmostEqual(qqq["shares"], 210)

    def test_sale_proceeds_not_redeployed_before_settlement(self):
        case = copy.deepcopy(self.plan)
        case["legs"][1]["face_value"] = 15000
        case["basket_preview"]["order_legs"] = copy.deepcopy(case["legs"])
        report = propose_multi_asset_batch(self.ledger, case)
        self.assertEqual(report["decision"], "BLOCK")
        self.assertIn("NEW_FINANCING_NOT_EXPLICITLY_AUTHORIZED", report["block_reasons"])
        self.assertEqual(report["pro_forma_unexecuted"]["unsettled_receivable"], 3499)

    def test_missing_basket_preview_blocks(self):
        self.plan.pop("basket_preview")
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertEqual(report["decision"], "BLOCK")
        self.assertIn("BASKET_WHATIF_PREVIEW_MISSING", report["block_reasons"])

    def test_preview_exact_leg_binding(self):
        self.plan["basket_preview"]["order_legs"][1]["face_value"] = 9000
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertEqual(report["decision"], "BLOCK")
        self.assertIn("BASKET_PREVIEW_NOT_BOUND_TO_EXACT_LEGS", report["block_reasons"])

    def test_preview_wrong_account_and_debt(self):
        self.plan["basket_preview"]["account_id"] = "WRONG"
        self.plan["basket_preview"]["margin_debt_after"] = 51000
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertIn("BASKET_PREVIEW_NOT_BOUND_TO_EXACT_LEGS", report["block_reasons"])
        self.assertIn("BASKET_PREVIEW_MARGIN_DEBT_MISMATCH", report["block_reasons"])

    def test_unauthorized_margin_borrowing_blocks(self):
        self.plan["legs"][1]["face_value"] = 16000
        self.plan["basket_preview"]["order_legs"] = copy.deepcopy(self.plan["legs"])
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertIn("NEW_FINANCING_NOT_EXPLICITLY_AUTHORIZED", report["block_reasons"])

    def test_broker_preview_not_accepted_blocks(self):
        self.plan["basket_preview"]["accepted"] = False
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertIn("BROKER_WHATIF_NOT_ACCEPTED_OR_STALE", report["block_reasons"])

    def test_option_permission_missing_blocks(self):
        self.plan["legs"][2]["trading_permission"] = False
        self.plan["basket_preview"]["order_legs"] = copy.deepcopy(self.plan["legs"])
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertIn("PROTECTIVE_PUT_PERMISSION_MISSING", report["block_reasons"])
        self.assertIn("OPTIONS_PERMISSION_MISSING", report["block_reasons"])

    def test_stale_option_quote_blocks(self):
        self.plan["legs"][2]["quote_age_seconds"] = 900
        self.plan["basket_preview"]["order_legs"] = copy.deepcopy(self.plan["legs"])
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertIn("PUT_CHAIN_QUOTE_STALE", report["block_reasons"])

    def test_underreported_risk_blocks(self):
        self.plan["risk_observation"]["pro_forma_stress_loss_fraction"] = .01
        self.plan["risk_observation"]["pro_forma_gross_leverage"] = .2
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertIn("PROFORMA_STRESS_UNDERREPORTED", report["block_reasons"])
        self.assertIn("PROFORMA_GROSS_UNDERREPORTED", report["block_reasons"])

    def test_unreconciled_ledger_blocks(self):
        self.ledger["accounts"][1]["broker_reported_net_liquidation"] = 123000
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertIn("LEDGER_NOT_READY_FOR_RISK_REVIEW", report["block_reasons"])

    def test_existing_short_option_requires_separate_broker_review(self):
        old = json.loads(
            (EXAMPLES / "institutional_ledger.json").read_text(encoding="utf-8")
        )
        # Point the plan at the original margin account, still carrying shorts.
        self.plan["broker_snapshot"]["net_liquidation"] = 117150
        report = propose_multi_asset_batch(old, self.plan)
        self.assertIn("LEDGER_NOT_READY_FOR_RISK_REVIEW", report["block_reasons"])

    def test_repayment_cannot_exceed_debt(self):
        self.plan["legs"][3]["amount"] = 60000
        with self.assertRaisesRegex(ValueError, "exceeds outstanding"):
            propose_multi_asset_batch(self.ledger, self.plan)

    def test_repayment_cannot_refinance_itself(self):
        self.plan["allow_margin_borrowing"] = True
        self.plan["legs"][3]["amount"] = 11000
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertIn("CANNOT_REBORROW_TO_SIMULATE_MARGIN_REPAYMENT", report["block_reasons"])

    def test_unsupported_or_duplicate_legs_rejected(self):
        self.plan["legs"][2]["action"] = "SELL_CALL"
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            propose_multi_asset_batch(self.ledger, self.plan)
        self.plan["legs"][2]["action"] = "BUY_PUT"
        self.plan["legs"][2]["leg_id"] = self.plan["legs"][0]["leg_id"]
        with self.assertRaisesRegex(ValueError, "Duplicate leg_id"):
            propose_multi_asset_batch(self.ledger, self.plan)

    def test_new_treasury_not_credited_collateral(self):
        self.plan["legs"][1]["face_value"] = 10000
        report = propose_multi_asset_batch(self.ledger, self.plan)
        self.assertAlmostEqual(
            report["pro_forma_unexecuted"]["broker_recognized_treasury_credit_report_only"],
            90000,
        )


if __name__ == "__main__":
    unittest.main()
