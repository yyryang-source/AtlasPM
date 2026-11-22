"""v0.4.12 multi-asset covariance, scenario and financing-budget tests."""

import copy
import json
import unittest
from pathlib import Path

from atlaspm.ibkr_gateway import FixtureGatewayTransport, collect_ibkr_readonly_account
from atlaspm.ibkr_financing import reconcile_ibkr_financing_margin
from atlaspm.ibkr_margin_monitor import collect_ibkr_margin_monitor
from atlaspm.ibkr_margin_stress import run_margin_stress
from atlaspm.ibkr_multiasset_budget import (
    _correlation_matrix, analyze_multiasset_risk_budget, redacted_multiasset_summary,
)

ROOT = Path(__file__).resolve().parents[2]


class MultiassetBudgetTests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads(
            (ROOT / "examples/ibkr_gateway_synthetic.json").read_text(encoding="utf-8")
        )
        capture = collect_ibkr_readonly_account(
            FixtureGatewayTransport(fixture), account_id="DU1234567", fixture_mode=True
        )
        monitor = collect_ibkr_margin_monitor(
            FixtureGatewayTransport(fixture), account_id="DU1234567", fixture_mode=True
        )
        mapping = json.loads(
            (ROOT / "examples/ibkr_mapping_synthetic.json").read_text(encoding="utf-8")
        )
        finance_input = json.loads(
            (ROOT / "examples/ibkr_financing_synthetic.json").read_text(encoding="utf-8")
        )
        finance = reconcile_ibkr_financing_margin(capture, mapping, finance_input)
        stress_config = json.loads(
            (ROOT / "examples/ibkr_margin_stress_synthetic.json").read_text(encoding="utf-8")
        )
        self.upstream = run_margin_stress(monitor, finance, stress_config)
        self.config = json.loads(
            (ROOT / "examples/ibkr_multiasset_budget_synthetic.json").read_text(encoding="utf-8")
        )

    def test_baseline_correlated_watch_severe_deficit_and_execution_never_enabled(self):
        report = analyze_multiasset_risk_budget(self.upstream, self.config)
        self.assertEqual(report["asset_sleeve_count"], 3)
        self.assertEqual(report["status"], "HYPOTHETICAL_RISK_BUDGET_ONLY")
        self.assertEqual(report["scenarios"][0]["state"], "HYPOTHETICAL_WITHIN_LIMITS")
        self.assertIn("PARAMETRIC_VAR_BUDGET_BREACH", report["scenarios"][1]["issues"])
        self.assertEqual(report["scenarios"][2]["state"], "HYPOTHETICAL_DEFICIT")
        self.assertIn("PROJECTED_MAINTENANCE_DEFICIT", report["scenarios"][2]["issues"])
        for flag in ("IBKR_financing_debt_separately_verified",
                     "broker_treasury_collateral_verified",
                     "IBKR_order_bound_whatif_verified",
                     "ready_for_institutional_risk_gate", "live_execution_enabled"):
            self.assertIs(report[flag], False)

    def test_deterministic_shocks_do_not_change_due_to_correlation(self):
        original = analyze_multiasset_risk_budget(self.upstream, self.config)
        config = copy.deepcopy(self.config)
        config["scenarios"][0]["correlation_override"] = [
            [1,1,1],[1,1,1],[1,1,1]
        ]
        changed = analyze_multiasset_risk_budget(self.upstream, config)
        self.assertAlmostEqual(
            original["scenarios"][0]["assumed_signed_market_pnl_usd"],
            changed["scenarios"][0]["assumed_signed_market_pnl_usd"],
        )
        self.assertGreater(
            changed["scenarios"][0]["one_day_99pct_delta_normal_var_usd"],
            original["scenarios"][0]["one_day_99pct_delta_normal_var_usd"],
        )

    def test_correlation_matrix_requires_valid_psd(self):
        bad_matrices = [
            [[1,.5,.6],[.4,1,.3],[.6,.3,1]],  # asymmetric
            [[1,.9,.9],[.9,1,-.9],[.9,-.9,1]],  # not PSD
            [[1,1.2,0],[1.2,1,0],[0,0,1]],
            [[1,0],[0,1]],
            [[True,0,0],[0,1,0],[0,0,1]],
        ]
        for matrix in bad_matrices:
            with self.subTest(matrix=str(matrix)), self.assertRaises(ValueError):
                _correlation_matrix(matrix, 3, "test")
        self.assertEqual(
            _correlation_matrix([[1,1],[1,1]], 2, "singular"),
            [[1.0,1.0],[1.0,1.0]],
        )

    def test_variance_matches_single_asset_closed_form(self):
        config = copy.deepcopy(self.config)
        config["assets"] = [{
            "id": "ONLY", "type": "equity", "market_value_usd":60000,
            "annual_volatility":.25
        }]
        config["base_correlation"] = [[1]]
        for scenario in config["scenarios"]:
            scenario["asset_returns"] = {"ONLY":-.20}
            scenario["correlation_override"] = [[1]]
        report = analyze_multiasset_risk_budget(self.upstream, config)
        first = report["scenarios"][0]
        self.assertAlmostEqual(
            first["one_day_99pct_delta_normal_var_usd"],
            60000 * .25 * 2.3263478740408408 / (252 ** .5),
        )

    def test_loan_interest_is_new_only_and_debt_not_subtracted_twice(self):
        report = analyze_multiasset_risk_budget(self.upstream, self.config)
        case = report["scenarios"][1]
        pnl = 25000 * -.20 + 20000 * -.35 + 15000 * -.15
        new_interest = 49950 * (.065 + .01) * 30 / 365
        self.assertAlmostEqual(case["assumed_signed_market_pnl_usd"], pnl)
        self.assertAlmostEqual(case["new_simple_financing_interest_usd"], new_interest)
        self.assertAlmostEqual(case["hypothetical_nlv_usd"], 123950 + pnl - new_interest)

    def test_gross_and_financing_cost_risk_budgets(self):
        config = copy.deepcopy(self.config)
        config["risk_limits"]["max_gross_exposure_to_nlv"] = 0.1
        config["risk_limits"]["max_assumed_annual_interest_to_nlv"] = 0.01
        report = analyze_multiasset_risk_budget(self.upstream, config)
        self.assertIn("GROSS_EXPOSURE_BUDGET_BREACH", report["scenarios"][0]["issues"])
        self.assertIn("ASSUMED_FINANCING_COST_BUDGET_BREACH", report["scenarios"][0]["issues"])

    def test_invalid_assumptions_and_missing_shocks_are_rejected(self):
        for field, value in (
            ("annual_volatility", True), ("market_value_usd", -1),
            ("annual_volatility", float("nan")),
        ):
            config = copy.deepcopy(self.config)
            config["assets"][0][field] = value
            with self.subTest(field=field, value=str(value)), self.assertRaises(ValueError):
                analyze_multiasset_risk_budget(self.upstream, config)
        config = copy.deepcopy(self.config)
        del config["scenarios"][0]["asset_returns"]["SYN_EQ_A"]
        with self.assertRaises(ValueError):
            analyze_multiasset_risk_budget(self.upstream, config)
        config = copy.deepcopy(self.config)
        config["assets"][0]["market_value_usd"] += 2
        with self.assertRaisesRegex(ValueError, "match prior"):
            analyze_multiasset_risk_budget(self.upstream, config)
        config = copy.deepcopy(self.config)
        config["scenarios"][0]["horizon_days"] = True
        with self.assertRaises(ValueError):
            analyze_multiasset_risk_budget(self.upstream, config)

    def test_rejects_unsupported_upstream_authority_and_corruption(self):
        upstream = copy.deepcopy(self.upstream)
        upstream["ready_for_institutional_risk_gate"] = True
        with self.assertRaises(ValueError):
            analyze_multiasset_risk_budget(upstream, self.config)
        upstream = copy.deepcopy(self.upstream)
        upstream["baseline_margin_usd_claim"]["available_funds"] += 5
        with self.assertRaises(ValueError):
            analyze_multiasset_risk_budget(upstream, self.config)
        upstream = copy.deepcopy(self.upstream)
        upstream["status"] = "BROKER_VERIFIED"
        with self.assertRaises(ValueError):
            analyze_multiasset_risk_budget(upstream, self.config)

    def test_redaction_does_not_print_asset_id_or_scenario_name(self):
        config = copy.deepcopy(self.config)
        config["assets"][0]["id"] = "SECRET_ACCOUNT_123"
        for case in config["scenarios"]:
            case["asset_returns"]["SECRET_ACCOUNT_123"] = case["asset_returns"].pop("SYN_EQ_A")
        config["scenarios"][0]["name"] = "SECRET_SCENARIO_456"
        result = analyze_multiasset_risk_budget(self.upstream, config)
        text = json.dumps(redacted_multiasset_summary(result, "local_data/budget.json"))
        for private in ("SECRET_ACCOUNT_123", "SECRET_SCENARIO_456", "123950", "49950"):
            self.assertNotIn(private, text)


if __name__ == "__main__":
    unittest.main()
