"""v0.4.9 offline IBKR financing/margin trust-boundary tests."""

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlaspm.ibkr_financing import reconcile_ibkr_financing_margin
from atlaspm.ibkr_gateway import FixtureGatewayTransport, collect_ibkr_readonly_account


ROOT = Path(__file__).resolve().parents[2]


class IBKRFinancingTests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads((ROOT / "examples/ibkr_gateway_synthetic.json").read_text(encoding="utf-8"))
        self.capture = collect_ibkr_readonly_account(
            FixtureGatewayTransport(fixture), account_id="DU1234567", fixture_mode=True
        )
        self.mapping = json.loads((ROOT / "examples/ibkr_mapping_synthetic.json").read_text(encoding="utf-8"))
        self.statement = json.loads((ROOT / "examples/ibkr_financing_synthetic.json").read_text(encoding="utf-8"))
        self.now = datetime.fromisoformat(self.capture["captured_at_utc"]).astimezone(timezone.utc)

    def evaluate(self, statement=None, mapping=None, capture=None, now=None):
        return reconcile_ibkr_financing_margin(
            capture if capture is not None else self.capture,
            mapping if mapping is not None else self.mapping,
            statement if statement is not None else self.statement,
            now_utc=now if now is not None else self.now,
        )

    def test_consistent_fields_never_grant_margin_or_live_execution(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "FIELDS_CONSISTENT_REVIEW_REQUIRED")
        self.assertEqual(result["check_issues"], [])
        self.assertEqual(result["mapping_status"], "IDENTITIES_MAPPED_REVIEW_REQUIRED")
        self.assertEqual(result["financing_claim_usd"]["loan_principal_from_statement"], 49950)
        for key in (
            "IBKR_financing_debt_separately_verified",
            "IBKR_margin_requirements_independently_verified",
            "broker_treasury_collateral_verified",
            "ready_for_institutional_risk_gate",
            "live_execution_enabled",
        ):
            self.assertFalse(result[key])

    def test_financing_debt_claims_mismatch_blocks(self):
        data = copy.deepcopy(self.statement)
        data["financing"]["loan_principal_from_activity_usd"] = 48000
        self.assertIn("FINANCING_PRINCIPAL_CLAIMS_MISMATCH", self.evaluate(data)["check_issues"])

    def test_gateway_nlv_mismatch_blocks(self):
        data = copy.deepcopy(self.statement)
        data["margin_summary"]["NetLiquidation"] = 111111
        self.assertIn("MARGIN_NLV_DIFFERS_FROM_GATEWAY", self.evaluate(data)["check_issues"])

    def test_margin_formulas_and_negative_liquidity_block(self):
        data = copy.deepcopy(self.statement)
        data["margin_summary"]["AvailableFunds"] = 25000
        data["margin_summary"]["ExcessLiquidity"] = -10
        issues = self.evaluate(data)["check_issues"]
        self.assertIn("AVAILABLE_FUNDS_FORMULA_MISMATCH", issues)
        self.assertIn("EXCESS_LIQUIDITY_FORMULA_MISMATCH", issues)
        self.assertIn("NONPOSITIVE_EXCESS_LIQUIDITY", issues)
        self.assertIn("MARGIN_BUFFER_BELOW_POLICY_FLOOR", issues)

    def test_stale_margin_and_capture_block(self):
        old = self.now + timedelta(seconds=700)
        issues = self.evaluate(now=old)["check_issues"]
        self.assertIn("MARGIN_SNAPSHOT_STALE", issues)
        self.assertIn("IBKR_CONTRACT_MAPPING_BLOCKED", issues)

    def test_temporal_skew_blocks(self):
        data = copy.deepcopy(self.statement)
        data["as_of_utc"] = (self.now - timedelta(seconds=500)).isoformat()
        issues = self.evaluate(data)["check_issues"]
        self.assertIn("MARGIN_CAPTURE_TIMESTAMP_SKEW", issues)

    def test_account_and_segment_mismatch_block(self):
        data = copy.deepcopy(self.statement)
        data["account_id"] = "SOME_OTHER_ACCOUNT"
        data["segment"] = "COMMODITIES"
        issues = self.evaluate(data)["check_issues"]
        self.assertIn("MARGIN_ACCOUNT_ID_MISMATCH", issues)
        self.assertIn("MARGIN_CURRENCY_OR_SEGMENT_UNSUPPORTED", issues)

    def test_mapping_review_failure_blocks(self):
        mapping = copy.deepcopy(self.mapping)
        mapping["contracts"][0]["manually_reviewed"] = False
        result = self.evaluate(mapping=mapping)
        self.assertEqual(result["status"], "BLOCK_FINANCING_MARGIN")
        self.assertIn("IBKR_CONTRACT_MAPPING_BLOCKED", result["check_issues"])

    def test_boolean_nan_negative_and_missing_numbers_fail_closed(self):
        for value in (True, float("nan"), -1, None):
            data = copy.deepcopy(self.statement)
            data["financing"]["loan_principal_from_activity_usd"] = value
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                self.evaluate(data)

    def test_cash_account_with_debt_blocks(self):
        capture = copy.deepcopy(self.capture)
        capture["raw"]["selected_account"]["tradingType"] = "CASH"
        self.assertIn("CASH_ACCOUNT_WITH_FINANCING_CLAIM", self.evaluate(capture=capture)["check_issues"])

    def test_negative_settled_cash_does_not_become_verified_debt(self):
        capture = copy.deepcopy(self.capture)
        capture["raw"]["USD_ledger"]["USD"]["settledcash"] = -42000
        result = self.evaluate(capture=capture)
        self.assertFalse(result["IBKR_financing_debt_separately_verified"])
        self.assertEqual(result["financing_claim_usd"]["loan_principal_from_statement"], 49950)

    def test_synthetic_timestamp_placeholder_cannot_be_used_with_real_source(self):
        capture = copy.deepcopy(self.capture)
        capture["source"] = "local_authenticated_ibkr_gateway"
        with self.assertRaises(ValueError):
            self.evaluate(capture=capture)


if __name__ == "__main__":
    unittest.main()
