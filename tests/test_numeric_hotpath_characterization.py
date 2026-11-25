"""Exact numerical regression checks for offline research hot paths."""
import random
import unittest
from math import sqrt
from statistics import fmean, stdev

from atlaspm.engine.mean_variance import optimize_mean_variance
from atlaspm.research.stock_walkforward import _performance, _ratio_drawdown


class NumericHotpathTests(unittest.TestCase):
    def test_performance_matches_original_expression(self):
        rng = random.Random(1729)
        for levels in ([1.0], [1.0, 1.1], [1.0] * 20,
                       [1.0 + rng.random() for _ in range(300)]):
            returns = [b / a - 1.0 if a else 0.0
                       for a, b in zip(levels, levels[1:])]
            years = len(returns) / 252.0
            expected = {
                "total_return": levels[-1] / levels[0] - 1.0,
                "cagr": (levels[-1] / levels[0]) ** (1.0 / years) - 1 if years > 0 else None,
                "annual_volatility": stdev(returns) * sqrt(252) if len(returns) > 1 else None,
                "sharpe_zero_risk_free": (
                    fmean(returns) * sqrt(252) / stdev(returns)
                    if len(returns) > 1 and stdev(returns) > 1e-12 else None
                ),
                "max_drawdown": _ratio_drawdown(levels),
                "return_observations": len(returns),
            }
            with self.subTest(length=len(levels)):
                self.assertEqual(_performance([{"equity": x} for x in levels], "equity"), expected)

    def test_symmetric_mean_variance(self):
        result = optimize_mean_variance({"A": 0.1, "B": 0.1},
                                        [[0.04, 0.0], [0.0, 0.04]])
        self.assertEqual(list(result["weights"]), ["A", "B"])
        for weight in result["weights"].values():
            self.assertAlmostEqual(weight, 0.5, places=14)
        self.assertEqual(result["iterations"], 1)
        self.assertAlmostEqual(result["annual_variance"], 0.02, places=14)


if __name__ == "__main__":
    unittest.main()
