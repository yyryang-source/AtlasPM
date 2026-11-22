"""v0.4.13 reverse margin stress: analytic thresholds and privacy tests."""

import copy
import json
import unittest
from pathlib import Path

from atlaspm.ibkr_gateway import FixtureGatewayTransport, collect_ibkr_readonly_account
from atlaspm.ibkr_financing import reconcile_ibkr_financing_margin
from atlaspm.ibkr_margin_monitor import collect_ibkr_margin_monitor
from atlaspm.ibkr_margin_stress import run_margin_stress
from atlaspm.ibkr_multiasset_budget import analyze_multiasset_risk_budget
from atlaspm.ibkr_reverse_stress import (
    _boundary, reverse_margin_stress, redacted_reverse_summary,
)

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "DU1234567"


class ReverseStressTests(unittest.TestCase):
    def setUp(self):
        gateway = json.loads(
            (ROOT / "examples/ibkr_gateway_synthetic.json").read_text(encoding="utf-8")
        )
        mapping = json.loads(
            (ROOT / "examples/ibkr_mapping_synthetic.json").read_text(encoding="utf-8")
        )
        financing_fixture = json.loads(
            (ROOT / "examples/ibkr_financing_synthetic.json").read_text(encoding="utf-8")
        )
        stress_fixture = json.loads(
            (ROOT / "examples/ibkr_margin_stress_synthetic.json").read_text(encoding="utf-8")
        )
        budget_fixture = json.loads(
            (ROOT / "examples/ibkr_multiasset_budget_synthetic.json").read_text(encoding="utf-8")
        )
        self.assumptions = json.loads(
            (ROOT / "examples/ibkr_reverse_stress_synthetic.json").read_text(encoding="utf-8")
        )
        capture = collect_ibkr_readonly_account(
            FixtureGatewayTransport(gateway), account_id=ACCOUNT, fixture_mode=True
        )
        monitor = collect_ibkr_margin_monitor(
            FixtureGatewayTransport(gateway), account_id=ACCOUNT, fixture_mode=True
        )
        finance = reconcile_ibkr_financing_margin(capture, mapping, financing_fixture)
        stress = run_margin_stress(monitor, finance, stress_fixture)
        self.upstream = analyze_multiasset_risk_budget(stress, budget_fixture)

    def compute(self, upstream=None, cases=None):
        return reverse_margin_stress(
            self.upstream if upstream is None else upstream,
            self.assumptions if cases is None else cases,
        )

    def test_four_synthetic_cases_and_zero_authority(self):
        report = self.compute()
        self.assertEqual(report["status"], "HYPOTHETICAL_REVERSE_STRESS_ONLY")
        self.assertEqual(len(report["cases"]), 4)
        self.assertEqual(report["cases"][0]["state"], "ILLUSTRATIVE_NO_THRESHOLD_IN_SCAN_RANGE")
        self.assertEqual(report["cases"][1]["state"], "ILLUSTRATIVE_THRESHOLD_WITHIN_SCAN")
        self.assertEqual(report["cases"][1]["first_threshold_codes"], ["POLICY_BUFFER"])
        self.assertEqual(report["cases"][2]["state"], "ILLUSTRATIVE_ALREADY_AT_THRESHOLD")
        self.assertEqual(report["cases"][2]["earliest_threshold_fraction"], 0)
        self.assertEqual(report["cases"][3]["state"], "ILLUSTRATIVE_NO_THRESHOLD_IN_SCAN_RANGE")
        for field in (
            "IBKR_financing_debt_separately_verified",
            "broker_treasury_collateral_verified",
            "IBKR_order_bound_whatif_verified",
            "ready_for_institutional_risk_gate",
            "live_execution_enabled",
        ):
            self.assertIs(report[field], False)

    def test_analytic_formula_for_thresholds(self):
        report = self.compute()
        case = report["cases"][1]
        interest = 49950 * .075 * 30 / 365
        elv = 110000 - interest
        nlv = 123950 - interest
        expected_margin = (elv - 30000*2) / 60000
        expected_buffer = (elv - 30000*2 - .15*nlv) / (60000*(1-.15))
        self.assertAlmostEqual(
            case["threshold_fractions"]["MAINTENANCE_EXCESS_ZERO"], expected_margin
        )
        self.assertAlmostEqual(case["threshold_fractions"]["POLICY_BUFFER"], expected_buffer)
        self.assertAlmostEqual(case["new_simple_interest_usd"], interest)

    def test_no_double_debit_of_existing_loan(self):
        report = self.compute()
        baseline = report["cases"][0]
        self.assertAlmostEqual(baseline["nlv_at_max_drawdown_usd"], 123950 - 60000)
        self.assertAlmostEqual(baseline["excess_liquidity_at_max_drawdown_usd"], 110000 - 30000 - 60000)

    def test_zero_shock_weight_no_threshold(self):
        report = self.compute()
        case = report["cases"][3]
        self.assertEqual(case["loss_per_100pct_benchmark_decline_usd"], 0)
        self.assertTrue(all(v is None for v in case["threshold_fractions"].values()))

    def test_crossing_boundary_helper(self):
        self.assertEqual(_boundary(-1, 100, 1), 0)
        self.assertEqual(_boundary(0, 100, 1), 0)
        self.assertIsNone(_boundary(2, 0, 1))
        self.assertIsNone(_boundary(150, 100, 1))
        self.assertAlmostEqual(_boundary(25, 100, 1), .25)
        self.assertIsNone(_boundary(25, 100, .2))

    def test_short_scan_cap_produces_no_threshold_in_range(self):
        cfg = copy.deepcopy(self.assumptions)
        cfg["maximum_drawdown_fraction"] = .05
        report = self.compute(cases=cfg)
        self.assertEqual(report["cases"][1]["state"], "ILLUSTRATIVE_NO_THRESHOLD_IN_SCAN_RANGE")
        self.assertEqual(report["cases"][2]["state"], "ILLUSTRATIVE_ALREADY_AT_THRESHOLD")

    def test_financing_budget_alert_is_separate_from_market_threshold(self):
        config = copy.deepcopy(self.assumptions)
        config["cases"][0]["assumed_annual_apr"] = 0.2
        output = self.compute(cases=config)
        self.assertEqual(
            output["cases"][0]["assumed_interest_budget_issue"],
            "ASSUMED_FINANCING_BUDGET_EXCEEDED",
        )
        self.assertEqual(output["cases"][0]["state"], "ILLUSTRATIVE_NO_THRESHOLD_IN_SCAN_RANGE")

    def test_skewed_shocks_change_threshold(self):
        cfg = copy.deepcopy(self.assumptions)
        cfg["cases"][1]["relative_shock_multipliers"]["SYN_EQ_B"] = 0
        after = self.compute(cases=cfg)
        before = self.compute()
        self.assertGreater(
            after["cases"][1]["threshold_fractions"]["POLICY_BUFFER"],
            before["cases"][1]["threshold_fractions"]["POLICY_BUFFER"],
        )

    def test_untrusted_report_claims_block(self):
        for field in (
            "ready_for_institutional_risk_gate",
            "live_execution_enabled",
            "IBKR_financing_debt_separately_verified",
        ):
            prior = copy.deepcopy(self.upstream)
            prior[field] = True
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.compute(upstream=prior)
        prior = copy.deepcopy(self.upstream)
        prior["status"] = "LIVE"
        with self.assertRaises(ValueError):
            self.compute(upstream=prior)
        prior = copy.deepcopy(self.upstream)
        prior["asset_sleeves"][0]["value_usd_assumption"] += 10
        with self.assertRaisesRegex(ValueError, "reconcile"):
            self.compute(upstream=prior)

    def test_bad_numeric_and_missing_shocks_block(self):
        for field, bad in (
            ("maintenance_multiplier", 0),
            ("horizon_days", True),
            ("horizon_days", 366),
            ("assumed_annual_apr", float("nan")),
            ("assumed_annual_apr", "0.05"),
        ):
            cfg = copy.deepcopy(self.assumptions)
            cfg["cases"][0][field] = bad
            with self.subTest(field=field, bad=str(bad)), self.assertRaises(ValueError):
                self.compute(cases=cfg)
        cfg = copy.deepcopy(self.assumptions)
        cfg["cases"][0]["relative_shock_multipliers"]["SYN_EQ_A"] = 1.1
        with self.assertRaises(ValueError):
            self.compute(cases=cfg)
        cfg = copy.deepcopy(self.assumptions)
        del cfg["cases"][0]["relative_shock_multipliers"]["SYN_EQ_A"]
        with self.assertRaisesRegex(ValueError, "Every sleeve"):
            self.compute(cases=cfg)

    def test_duplicate_case_name_and_invalid_floor_block(self):
        cfg = copy.deepcopy(self.assumptions)
        cfg["cases"][1]["name"] = cfg["cases"][0]["name"]
        with self.assertRaises(ValueError):
            self.compute(cases=cfg)
        cfg = copy.deepcopy(self.assumptions)
        cfg["min_excess_to_nlv_ratio"] = 1
        with self.assertRaises(ValueError):
            self.compute(cases=cfg)

    def test_console_never_displays_account_amounts_or_case_names(self):
        cfg = copy.deepcopy(self.assumptions)
        cfg["cases"][0]["name"] = "SENSITIVE_987654"
        result = self.compute(cases=cfg)
        redacted = json.dumps(redacted_reverse_summary(result, "local_data/reverse.json"))
        for text in ("DU1234567", "SENSITIVE_987654", "123950", "49950"):
            self.assertNotIn(text, redacted)


if __name__ == "__main__":
    unittest.main()
