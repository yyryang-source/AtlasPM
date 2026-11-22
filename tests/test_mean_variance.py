"""Mean-variance optimizer validation and accounting invariants."""

import math
import unittest

from atlaspm.mean_variance import optimize_mean_variance
from atlaspm.models import PortfolioAccount, Position


class MeanVarianceTest(unittest.TestCase):
    def test_feasible_capped_portfolio(self) -> None:
        report = optimize_mean_variance(
            {"A": 0.09, "B": 0.07, "C": 0.10},
            [[0.04, 0.008, 0.01], [0.008, 0.025, 0.005],
             [0.01, 0.005, 0.09]],
            max_weight=0.45,
        )
        weights = report["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=9)
        self.assertTrue(all(0 <= w <= 0.45 + 1e-9 for w in weights.values()))
        self.assertAlmostEqual(
            sum(report["variance_contributions"].values()),
            report["annual_variance"], places=9
        )

    def test_more_aversion_reduces_risk(self) -> None:
        args = (
            {"A": 0.15, "B": 0.04},
            [[0.16, 0.0], [0.0, 0.01]],
        )
        low = optimize_mean_variance(*args, risk_aversion=0.5, max_weight=1)
        high = optimize_mean_variance(*args, risk_aversion=20, max_weight=1)
        self.assertLessEqual(
            high["expected_annual_volatility"],
            low["expected_annual_volatility"] + 1e-9
        )

    def test_leverage_financing_identity(self) -> None:
        report = optimize_mean_variance(
            {"A": 0.10, "B": 0.07},
            [[0.04, 0.0], [0.0, 0.01]],
            leverage=2, annual_financing_rate=0.08,
        )
        self.assertAlmostEqual(
            report["expected_net_return"],
            report["expected_gross_return"] - 0.08,
        )

    def test_rejects_invalid_covariance(self) -> None:
        mu = {"A": 0.08, "B": 0.10}
        for covariance in (
            [[0.04, 0.03], [0.01, 0.04]],
            [[0.04, 0.20], [0.20, 0.04]],
            [[0.04, float("nan")], [float("nan"), 0.04]],
            [[0.04]],
        ):
            with self.subTest(covariance=covariance):
                with self.assertRaises(ValueError):
                    optimize_mean_variance(mu, covariance)

    def test_rejects_infeasible_cap(self) -> None:
        with self.assertRaises(ValueError):
            optimize_mean_variance(
                {"A": 0.10, "B": 0.09},
                [[0.02, 0], [0, 0.03]], max_weight=0.40
            )

    def test_rejects_impossible_leverage(self) -> None:
        with self.assertRaises(ValueError):
            optimize_mean_variance(
                {"A": 0.10}, [[0.04]], leverage=5
            )

    def test_cash_account_has_no_margin_requirement(self) -> None:
        account = PortfolioAccount(
            name="cash", account_type="cash", cash=0, debt=0,
            positions=(Position("QQQ", 100),),
        )
        self.assertEqual(account.maintenance_required, 0.0)
        self.assertEqual(account.stress(0.30)["maintenance_required"], 0.0)


if __name__ == "__main__":
    unittest.main()
