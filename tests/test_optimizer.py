import unittest

from atlaspm.optimizer import inverse_volatility_weights


class OptimizerTest(unittest.TestCase):
    def test_inverse_volatility(self) -> None:
        weights = inverse_volatility_weights(
            {"QQQ": 0.20, "NVDA": 0.40}, max_weight=0.8
        )
        self.assertAlmostEqual(weights["QQQ"], 2 / 3)
        self.assertAlmostEqual(weights["NVDA"], 1 / 3)
        self.assertAlmostEqual(sum(weights.values()), 1)

    def test_concentration_cap(self) -> None:
        weights = inverse_volatility_weights(
            {"A": 0.01, "B": 0.20, "C": 0.30}, max_weight=0.5
        )
        self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertTrue(all(0 <= v <= 0.5 + 1e-12 for v in weights.values()))
        self.assertAlmostEqual(weights["A"], 0.5)

    def test_cap_not_feasible(self) -> None:
        with self.assertRaises(ValueError):
            inverse_volatility_weights({"A": 0.2, "B": 0.3}, max_weight=0.4)

    def test_invalid_inputs(self) -> None:
        for vols in ({}, {"A": 0.0}, {"A": float("nan")}):
            with self.subTest(vols=vols):
                with self.assertRaises(ValueError):
                    inverse_volatility_weights(vols)


if __name__ == "__main__":
    unittest.main()
