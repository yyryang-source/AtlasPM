"""Offline policy tests for institutional intent preflight (no broker traffic)."""

import copy
import json
import unittest
from pathlib import Path

from atlaspm.institutional_gate import assess_institutional_intent


SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "institutional_scenario.json"


class InstitutionalGateTests(unittest.TestCase):
    def setUp(self):
        self.case = json.loads(SAMPLE.read_text(encoding="utf-8"))

    def test_paper_only_normal_add(self):
        result = assess_institutional_intent(self.case)
        self.assertEqual(result["risk_state"], "NORMAL")
        self.assertEqual(result["decision"], "ALLOW_PAPER_INTENT")
        self.assertTrue(result["broker_preview_verified"])
        self.assertFalse(result["live_execution_enabled"])
        self.assertAlmostEqual(result["broker_recognized_treasury_collateral_value"], 39000)

    def test_stale_snapshot_halts(self):
        case = copy.deepcopy(self.case)
        case["account"]["data_age_seconds"] = 180
        result = assess_institutional_intent(case)
        self.assertEqual(result["risk_state"], "HALT")
        self.assertIn("BROKER_SNAPSHOT_STALE", result["block_reasons"])
        self.assertEqual(result["decision"], "BLOCK")

    def test_unreconciled_account_halts(self):
        case = copy.deepcopy(self.case)
        case["account"]["reconciled"] = False
        result = assess_institutional_intent(case)
        self.assertEqual(result["risk_state"], "HALT")
        self.assertIn("ACCOUNT_NOT_RECONCILED", result["block_reasons"])

    def test_drawdown_watch_and_reduce_states(self):
        for drawdown, state in ((0.12, "WATCH"), (0.22, "DE_RISK"), (0.35, "CRITICAL")):
            with self.subTest(drawdown=drawdown):
                case = copy.deepcopy(self.case)
                case["risk_observation"]["current_drawdown_fraction"] = drawdown
                result = assess_institutional_intent(case)
                self.assertEqual(result["risk_state"], state)
                self.assertIn("RISK_STATE_BLOCKS_ADDITIONS", result["block_reasons"])

    def test_risk_reduction_in_derisk(self):
        case = copy.deepcopy(self.case)
        case["risk_observation"]["current_drawdown_fraction"] = .21
        case["intent"]["action"] = "REDUCE_EQUITY"
        result = assess_institutional_intent(case)
        self.assertEqual(result["risk_state"], "DE_RISK")
        self.assertEqual(result["decision"], "ALLOW_PAPER_INTENT")

    def test_emergency_reduce_requires_manual_review(self):
        case = copy.deepcopy(self.case)
        case["risk_observation"]["current_drawdown_fraction"] = .31
        case["intent"]["action"] = "REDUCE_EQUITY"
        result = assess_institutional_intent(case)
        self.assertEqual(result["decision"], "BLOCK")
        self.assertIn("REDUCTION_REQUIRES_OPERATOR_REVIEW", result["warnings"])

    def test_broker_preview_missing_fails_closed(self):
        case = copy.deepcopy(self.case)
        case.pop("broker_preview")
        result = assess_institutional_intent(case)
        self.assertIn("BROKER_WHATIF_PREVIEW_MISSING", result["block_reasons"])

    def test_broker_preview_below_liquidity_limit(self):
        case = copy.deepcopy(self.case)
        case["broker_preview"]["excess_liquidity_after"] = 3000
        result = assess_institutional_intent(case)
        self.assertIn("INSUFFICIENT_POSTTRADE_BROKER_LIQUIDITY", result["block_reasons"])

    def test_unconfirmed_treasury_collateral_fails(self):
        case = copy.deepcopy(self.case)
        case["intent"].update({
            "relies_on_treasury_collateral": True,
            "required_treasury_credit": 20000,
        })
        case["treasury_collateral"][0]["broker_eligible"] = None
        result = assess_institutional_intent(case)
        self.assertIn("TREASURY_COLLATERAL_UNVERIFIED", result["block_reasons"])
        self.assertIn("TREASURY_COLLATERAL_INSUFFICIENT", result["block_reasons"])

    def test_broker_confirmed_collateral_supports_paper_proposal(self):
        case = copy.deepcopy(self.case)
        case["intent"].update({
            "relies_on_treasury_collateral": True,
            "required_treasury_credit": 20000,
        })
        result = assess_institutional_intent(case)
        self.assertEqual(result["decision"], "ALLOW_PAPER_INTENT")

    def test_options_hedge_quote_and_permission(self):
        case = copy.deepcopy(self.case)
        case["intent"]["action"] = "BUY_PROTECTIVE_PUT"
        case["option_hedge"] = {
            "trading_permission": True,
            "quote_age_seconds": 10,
            "premium_and_fees": 1200
        }
        result = assess_institutional_intent(case)
        self.assertEqual(result["decision"], "ALLOW_PAPER_INTENT")
        case["option_hedge"]["trading_permission"] = False
        bad = assess_institutional_intent(case)
        self.assertIn("OPTIONS_PERMISSION_MISSING", bad["block_reasons"])

    def test_option_premium_and_collar_rules(self):
        case = copy.deepcopy(self.case)
        case["intent"]["action"] = "OPEN_COLLAR"
        case["option_hedge"] = {
            "trading_permission": True,
            "quote_age_seconds": 10,
            "premium_and_fees": 5000,
            "short_call_is_covered": False,
            "spread_max_loss_bounded": False
        }
        result = assess_institutional_intent(case)
        self.assertIn("OPTION_PREMIUM_BUDGET_EXCEEDED", result["block_reasons"])
        self.assertIn("UNCOVERED_SHORT_CALL_NOT_ALLOWED", result["block_reasons"])
        self.assertIn("UNBOUNDED_OPTION_SPREAD_RISK", result["block_reasons"])

    def test_cash_account_cannot_borrow(self):
        case = copy.deepcopy(self.case)
        case["account"]["type"] = "cash"
        case["account"]["margin_debt"] = 0
        case["broker_preview"]["margin_debt_after"] = 100
        result = assess_institutional_intent(case)
        self.assertIn("CASH_ACCOUNT_BORROWING_PROHIBITED", result["block_reasons"])

    def test_loan_debt_in_cash_halts(self):
        case = copy.deepcopy(self.case)
        case["account"]["type"] = "cash"
        result = assess_institutional_intent(case)
        self.assertEqual(result["risk_state"], "HALT")
        self.assertIn("CASH_ACCOUNT_HAS_MARGIN_DEBT", result["block_reasons"])

    def test_add_leverage_and_stress_limit(self):
        case = copy.deepcopy(self.case)
        case["risk_observation"]["pro_forma_stress_loss_fraction"] = .55
        case["risk_observation"]["pro_forma_gross_leverage"] = 2.4
        result = assess_institutional_intent(case)
        self.assertIn("STRESS_LOSS_BUDGET_EXCEEDED", result["block_reasons"])
        self.assertIn("GROSS_LEVERAGE_BUDGET_EXCEEDED", result["block_reasons"])


if __name__ == "__main__":
    unittest.main()
