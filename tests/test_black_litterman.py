"""Numeric oracle, PIT and end-to-end allocation checks for explicit BL views."""
from copy import deepcopy
from importlib.util import find_spec
import json
from pathlib import Path
import unittest

from atlaspm.engine.allocation import black_litterman_posterior
from atlaspm.engine.unified import run_unified_portfolio
from atlaspm.engine.replay import replay
from test_historical_replay_allocation import rolling_fixture


@unittest.skipUnless(find_spec("numpy") and find_spec("scipy"), "portfolio extra required")
class BlackLittermanTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            "market_weights":{"AAA":1.}, "risk_aversion":2.5, "tau":.05,
            "risk_free_rate":.02, "available_at":"2020-01-01T00:00:00Z",
            "evidence_sha256":"a"*64,
            "views":[{"id":"return", "weights":{"AAA":1.},
                "expected_excess_return":.2, "uncertainty_variance":.002,
                "available_at":"2020-01-01T00:00:00Z", "expires_at":"2021-01-01T00:00:00Z",
                "evidence_sha256":"b"*64}]}

    def compute(self):
        return black_litterman_posterior(["AAA"], [[.04]], self.spec,
                                         as_of="2020-02-01T00:00:00Z")

    def test_scalar_bayesian_weighted_mean(self):
        result = self.compute()
        self.assertAlmostEqual(result["prior_excess_returns"]["AAA"], .1)
        self.assertAlmostEqual(result["posterior_excess_returns"]["AAA"], .15)
        self.assertAlmostEqual(result["expected_returns"]["AAA"], .17)
        self.assertAlmostEqual(result["covariance"][0][0], .041)
        self.assertFalse(result["source_authenticity_verified"])

    def test_no_views_preserves_equilibrium_and_mean_uncertainty(self):
        self.spec["views"] = []
        result = self.compute()
        self.assertAlmostEqual(result["expected_returns"]["AAA"], .12)
        self.assertAlmostEqual(result["covariance"][0][0], .042)

    def test_less_certain_views_have_less_influence(self):
        baseline = self.compute()
        self.spec["views"][0]["uncertainty_variance"] = 50.
        result = self.compute()
        self.assertLess(abs(result["posterior_excess_returns"]["AAA"]-.1),
                        abs(baseline["posterior_excess_returns"]["AAA"]-.1))

    def test_multivariate_matches_precision_space_oracle(self):
        import numpy as np
        spec = rolling_fixture("black_litterman")["optimizer"]["black_litterman"]
        cov = np.array([[.04,.01],[.01,.09]])
        p = np.array([[1., -1.]])
        prior = spec["risk_aversion"]*cov@np.array([.5,.5])
        precision = np.linalg.inv(spec["tau"]*cov)
        omega = np.array([[.01]])
        mean_cov = np.linalg.inv(precision+p.T@np.linalg.inv(omega)@p)
        expected = mean_cov@(precision@prior+p.T@np.linalg.inv(omega)@np.array([.03]))
        out = black_litterman_posterior(["AAA","BBB"],cov.tolist(),spec,as_of="2020-02-01T00:00:00Z")
        np.testing.assert_allclose(list(out["posterior_excess_returns"].values()), expected, rtol=1e-12)
        np.testing.assert_allclose(out["covariance"], cov+mean_cov, rtol=1e-12)

    def test_numeric_scope_and_pit_fail_closed(self):
        mutations = [
            lambda x:x.update(tau=True), lambda x:x.update(tau=0),
            lambda x:x.update(risk_free_rate=float("nan")),
            lambda x:x.update(market_weights={"AAA":.8}),
            lambda x:x.update(available_at="2021-01-01T00:00:00Z"),
            lambda x:x.update(evidence_sha256="missing"),
            lambda x:x["views"][0].update(available_at="2020-03-01T00:00:00Z"),
            lambda x:x["views"][0].update(expires_at="2020-02-01T00:00:00Z"),
            lambda x:x["views"][0].update(weights={"BBB":1}),
            lambda x:x["views"][0].update(weights={"AAA":2}),
            lambda x:x["views"][0].update(uncertainty_variance=0),
            lambda x:x["views"].append(deepcopy(x["views"][0])),
        ]
        original = deepcopy(self.spec)
        for mutate in mutations:
            self.spec = deepcopy(original)
            mutate(self.spec)
            with self.subTest(spec=self.spec), self.assertRaises(ValueError):
                self.compute()

    def test_integrated_oms_and_independent_risk_veto(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root/"examples/unified_portfolio_synthetic.json").read_text())
        data["constraints"]["method"] = "black_litterman"
        spec = rolling_fixture("black_litterman")["optimizer"]["black_litterman"]
        spec["views"][0]["expires_at"] = "2027-01-01T00:00:00Z"
        data["market"]["black_litterman"] = spec
        out = run_unified_portfolio(data)
        self.assertEqual(out["decision"], "SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE", out)
        self.assertEqual(out["allocation"]["method"], "black_litterman")
        self.assertEqual(out["institutional_preflight"]["decision"], "ALLOW_PAPER_INTENT")
        self.assertTrue(all(out["reconciliation_checks"].values()))
        self.assertFalse(out["live_execution_enabled"])
        data["constraints"]["max_annual_volatility"] = .001
        blocked = run_unified_portfolio(data)
        self.assertEqual(blocked["decision"], "BLOCK")
        self.assertIn("PORTFOLIO_VOLATILITY_LIMIT", blocked["block_reasons"])

    def test_rolling_replay_prefix_invariance(self):
        data = rolling_fixture("black_litterman")
        full = replay(data)
        self.assertTrue(full["allocations"])
        self.assertTrue(all(a["black_litterman"]["view_ids"] for a in full["allocations"]))
        data["sessions"] = data["sessions"][:24]
        short = replay(data)
        self.assertEqual(full["curve"][:24], short["curve"])
        self.assertEqual(full["allocations"][:len(short["allocations"])], short["allocations"])
        data["optimizer"]["black_litterman"]["available_at"] = "2025-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "FUTURE_PRIOR"):
            replay(data)
