"""Numerical checks for pretrade risk budgets."""
import unittest

from atlaspm.risk.risk_budget import check_risk_budget


class RiskBudgetTests(unittest.TestCase):
    def setUp(self):
        self.weights = {"A": 0.5, "B": 0.5}
        self.cov = [[0.04, 0], [0, 0.04]]

    def test_two_times_fails_30pct_uniform_shock_margin(self):
        r = check_risk_budget(self.weights, self.cov, proposed_leverage=2)
        self.assertFalse(r["passes_all_checks"])
        self.assertIn("minimum_stress_maintenance_excess", r["failed_checks"])
        self.assertAlmostEqual(r["stressed_equity_fraction_of_initial"], 0.4)
        self.assertAlmostEqual(r["stressed_maintenance_excess_fraction_of_initial"], -0.02)
        self.assertLessEqual(r["conservative_gross_leverage_cap_under_these_assumptions"], 1.4)

    def test_one_times_no_margin_and_reasonable_constraints(self):
        r = check_risk_budget(self.weights, self.cov, proposed_leverage=1)
        self.assertTrue(r["passes_all_checks"])
        self.assertEqual(r["stressed_maintenance_required_fraction_of_initial"], 0)
        self.assertAlmostEqual(r["estimated_unlevered_annual_volatility"], 0.141421356, places=7)

    def test_name_concentration(self):
        r = check_risk_budget({"A": .9, "B": .1}, self.cov, proposed_leverage=1)
        self.assertIn("maximum_name_weight", r["failed_checks"])
        self.assertIsNone(r["conservative_gross_leverage_cap_under_these_assumptions"])

    def test_invalid_covariance(self):
        with self.assertRaisesRegex(ValueError, "semidefinite"):
            check_risk_budget(self.weights, [[.04, .5], [.5, .04]])

    def test_bad_weights_or_leverage(self):
        with self.assertRaisesRegex(ValueError, "sum"):
            check_risk_budget({"A": .8, "B": .5}, self.cov)
        with self.assertRaises(ValueError):
            check_risk_budget(self.weights, self.cov, proposed_leverage=8)

    def test_volatility_cap_unavailable_even_at_1x(self):
        r = check_risk_budget(self.weights, self.cov, proposed_leverage=1, max_annual_volatility=.10)
        self.assertTrue(r["no_fully_invested_1x_solution_meets_all_limits"])
        self.assertIsNone(r["conservative_gross_leverage_cap_under_these_assumptions"])

    def test_margin_only_bound_below_one_allows_unlevered(self):
        r = check_risk_budget(
            self.weights, self.cov, proposed_leverage=1,
            max_stress_loss_fraction=.50, maintenance_fraction=1.0
        )
        self.assertTrue(r["passes_all_checks"])
        self.assertEqual(r["conservative_gross_leverage_cap_under_these_assumptions"], 1.0)


if __name__ == "__main__":
    unittest.main()
