"""Check that stock allocations use correlations and enforce joint limits."""

from importlib.util import find_spec
import unittest

from atlaspm.engine.stock_allocation import StockAllocationSettings, StockAllocationSignal, optimize_stock_allocation


@unittest.skipUnless(find_spec("scipy"), "Optional portfolio dependency")
class StockAllocationTest(unittest.TestCase):
    def test_correlations_and_sector_membership_change_weights(self):
        import numpy as np

        symbols = ("A", "B", "C")
        sectors = ("Tech", "Tech", "Health")
        independent = np.eye(3) * .05
        correlated = independent.copy()
        correlated[0, 1] = correlated[1, 0] = .045
        settings = StockAllocationSettings(max_weight=1, rank_tilt=0)
        options = {"cost_rate": 0, "holding_sessions": 21}

        def run(covariance, limits):
            signal = StockAllocationSignal(symbols, (50, 50, 50), sectors, covariance)
            weights, status = optimize_stock_allocation(signal, {}, set(symbols), limits, **options)
            self.assertEqual(status, "OPTIMIZED")
            return weights

        equal = run(independent, settings)
        diversified = run(correlated, settings)
        self.assertAlmostEqual(equal["C"], 1 / 3, places=5)
        self.assertGreater(diversified["C"], equal["C"])
        capped = run(independent, StockAllocationSettings(max_weight=.6, sector_cap=.45, rank_tilt=0))
        self.assertAlmostEqual(capped["A"] + capped["B"], .45, places=6)
        self.assertLessEqual(capped["C"], .60000001)

    def test_shared_covariance_matches_sample_and_preserves_diagonal(self):
        import numpy as np
        from atlaspm.data.historical import rolling_return_estimates
        from atlaspm.engine.mean_variance import _validate_covariance
        rng=np.random.default_rng(9)
        rows=rng.normal(0,.02,(60,3))
        _,raw,_=rolling_return_estimates(rows.tolist(),["A","B","C"])
        np.testing.assert_allclose(raw,np.cov(rows,rowvar=False,ddof=1)*252,atol=1e-12)
        _,shrunk,_=rolling_return_estimates(rows.tolist(),["A","B","C"],shrinkage=.25)
        np.testing.assert_allclose(shrunk,.75*np.asarray(raw)+.25*np.diag(np.diag(raw)),atol=1e-12)
        _,weighted,meta=rolling_return_estimates(rows.tolist(),["A","B","C"],
                                                shrinkage=.25,half_life_sessions=10)
        _validate_covariance(weighted,3)
        self.assertLess(meta["effective_observations"],60)
        self.assertFalse(meta["historical_mean_is_forecast"])
        with self.assertRaises(ValueError):
            rolling_return_estimates([[0.,0.],[.1,.1],[float("nan"),.1]],["A","B"])

    def test_reports_why_incompatible_caps_skip_the_trade(self):
        import numpy as np

        signal = StockAllocationSignal(("A",), (60,), ("Tech",), np.array([[.01]]))
        settings = StockAllocationSettings(max_weight=.4, turnover_limit=.15)
        options = {"settings": settings, "cost_rate": .001, "holding_sessions": 21}
        self.assertEqual(optimize_stock_allocation(signal, {"A": 1.0}, {"A"}, **options),
                         (None, "CONCENTRATION_TURNOVER_CONFLICT"))
        self.assertEqual(optimize_stock_allocation(signal, {"B": .6}, {"A"}, **options),
                         (None, "FORCED_EXITS_EXCEED_TURNOVER_LIMIT"))
