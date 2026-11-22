"""v0.4.11 static stress tests: all broker data and loan records are synthetic."""

import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from atlaspm.ibkr_financing import reconcile_ibkr_financing_margin
from atlaspm.ibkr_gateway import (
    FixtureGatewayTransport, collect_ibkr_readonly_account,
)
from atlaspm.ibkr_margin_monitor import collect_ibkr_margin_monitor
from atlaspm.ibkr_margin_stress import run_margin_stress, redacted_stress_console

ROOT = Path(__file__).resolve().parents[1]


class OfflineMarginStressTests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads(
            (ROOT / "examples/ibkr_gateway_synthetic.json").read_text(encoding="utf-8")
        )
        self.capture = collect_ibkr_readonly_account(
            FixtureGatewayTransport(fixture), account_id="DU1234567", fixture_mode=True
        )
        self.monitor = collect_ibkr_margin_monitor(
            FixtureGatewayTransport(fixture), account_id="DU1234567", fixture_mode=True
        )
        mapping = json.loads(
            (ROOT / "examples/ibkr_mapping_synthetic.json").read_text(encoding="utf-8")
        )
        statement = json.loads(
            (ROOT / "examples/ibkr_financing_synthetic.json").read_text(encoding="utf-8")
        )
        self.finance = reconcile_ibkr_financing_margin(
            self.capture, mapping, statement
        )
        self.assumptions = json.loads(
            (ROOT / "examples/ibkr_margin_stress_synthetic.json").read_text(encoding="utf-8")
        )

    def simulate(self, monitor=None, finance=None, assumptions=None):
        return run_margin_stress(
            self.monitor if monitor is None else monitor,
            self.finance if finance is None else finance,
            self.assumptions if assumptions is None else assumptions,
        )

    def test_synthetic_baseline_and_debt_are_only_claims(self):
        report = self.simulate()
        self.assertEqual(report["status"], "HYPOTHETICAL_STRESS_ONLY")
        self.assertEqual(len(report["scenarios"]), 4)
        self.assertEqual(report["scenarios"][0]["scenario_state"], "ILLUSTRATIVE_ABOVE_FLOOR")
        for key in (
            "IBKR_financing_debt_separately_verified",
            "broker_treasury_collateral_verified",
            "IBKR_order_bound_whatif_verified",
            "ready_for_institutional_risk_gate",
            "live_execution_enabled",
        ):
            self.assertFalse(report[key])

    def test_large_shock_and_house_requirement_jump_cause_deficit(self):
        report = self.simulate()
        worst = report["scenarios"][-1]
        self.assertEqual(worst["scenario_state"], "ILLUSTRATIVE_DEFICIT")
        self.assertIn("PROJECTED_MAINTENANCE_DEFICIT", worst["scenario_issues"])
        self.assertGreater(worst["new_simple_financing_interest_usd"], 0)

    def test_interest_formula_and_not_subtracting_old_accrual(self):
        f = copy.deepcopy(self.finance)
        f["financing_claim_usd"]["accrued_interest_from_statement"] = 1200
        report = self.simulate(finance=f)
        row = report["scenarios"][1]
        expected = 49950 * .065 * 30 / 365
        self.assertAlmostEqual(row["new_simple_financing_interest_usd"], expected)
        self.assertAlmostEqual(
            row["hypothetical_nlv_usd"], 123950 - 60000 * .2 - expected
        )
        self.assertEqual(report["existing_accrued_interest_usd_claim_not_double_counted"], 1200)

    def test_zero_apr_zero_loan_or_zero_horizon_cost(self):
        assumption = copy.deepcopy(self.assumptions)
        assumption["assumed_annual_financing_apr"] = 0
        report = self.simulate(assumptions=assumption)
        self.assertTrue(all(s["new_simple_financing_interest_usd"] == 0 for s in report["scenarios"]))
        assumption["assumed_annual_financing_apr"] = 0.065
        f = copy.deepcopy(self.finance)
        f["financing_claim_usd"]["loan_principal_from_statement"] = 0
        f["financing_claim_usd"]["loan_principal_from_activity"] = 0
        report = self.simulate(finance=f)
        self.assertTrue(all(s["new_simple_financing_interest_usd"] == 0 for s in report["scenarios"]))

    def test_reject_inconsistent_upstream_baseline(self):
        f = copy.deepcopy(self.finance)
        f["margin_summary_claim_usd"]["NetLiquidation"] += 5
        with self.assertRaisesRegex(ValueError, "baseline margin"):
            self.simulate(finance=f)
        f = copy.deepcopy(self.finance)
        f["financing_claim_usd"]["loan_principal_from_activity"] += 10
        with self.assertRaisesRegex(ValueError, "loan claims mismatch"):
            self.simulate(finance=f)

    def test_reject_upstream_blocked_monitor_or_claims(self):
        m = copy.deepcopy(self.monitor)
        m["monitor_state"] = "BLOCKED_DATA"
        with self.assertRaisesRegex(ValueError, "Monitor data"):
            self.simulate(monitor=m)
        f = copy.deepcopy(self.finance)
        f["status"] = "BLOCK_FINANCING_MARGIN"
        with self.assertRaisesRegex(ValueError, "Financing claims"):
            self.simulate(finance=f)
        f = copy.deepcopy(self.finance)
        f["live_execution_enabled"] = True
        with self.assertRaisesRegex(ValueError, "authorize execution"):
            self.simulate(finance=f)

    def test_reject_account_mismatch_or_source_mixing(self):
        f = copy.deepcopy(self.finance)
        f["account_id"] = "DIFFERENT"
        with self.assertRaisesRegex(ValueError, "account identity"):
            self.simulate(finance=f)
        m = copy.deepcopy(self.monitor)
        m["source"] = "local_authenticated_ibkr_gateway"
        with self.assertRaisesRegex(ValueError, "mixed"):
            self.simulate(monitor=m)

    def test_scenario_bad_ranges_boolean_nan_and_duplicate_name(self):
        for key, value in (
            ("shock_fraction", True), ("shock_fraction", -0.1),
            ("shock_fraction", 1.1), ("shock_fraction", float("nan")),
            ("maintenance_multiplier", 0.5), ("maintenance_multiplier", 6),
            ("horizon_days", -1), ("horizon_days", 366),
            ("horizon_days", 1.5)
        ):
            assumption = copy.deepcopy(self.assumptions)
            assumption["scenarios"][0][key] = value
            with self.subTest(key=key, value=str(value)), self.assertRaises(ValueError):
                self.simulate(assumptions=assumption)
        assumption = copy.deepcopy(self.assumptions)
        assumption["scenarios"][1]["name"] = assumption["scenarios"][0]["name"]
        with self.assertRaises(ValueError):
            self.simulate(assumptions=assumption)

    def test_private_console_masks_identifiers_prices_and_scenario_names(self):
        assumption = copy.deepcopy(self.assumptions)
        assumption["scenarios"][0]["name"] = "DU1234567 49950 private"
        report = self.simulate(assumptions=assumption)
        text = json.dumps(redacted_stress_console(report, "local_data/result.json"))
        for secret in ("DU1234567", "49950", "123950", "60000", "private"):
            self.assertNotIn(secret, text)
        self.assertEqual(report["scenarios"][0]["name"], "DU1234567 49950 private")


if __name__ == "__main__":
    unittest.main()
