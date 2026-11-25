"""Regression tests for modeled cash replay and Treasury curve sensitivity."""
from copy import deepcopy
import unittest

from atlaspm.engine import replay_review as r
from atlaspm.strategies.bonds.treasury_replay import curve_shock,_value_at_yield
from atlaspm.research.alpha_replay_bridge import RankReplay
from .test_alpha_replay_bridge import execution,WEIGHTS
from .test_alpha_multi_horizon import panel


class ReviewTests(unittest.TestCase):
    def prepared(self):
        p=panel()
        b=RankReplay(p,60,(1,5,21),WEIGHTS,execution(p))
        for h in (1,5,21):
            b.add(h,[{"date":d,"security_id":sid,"prediction":i}
                     for d in b.common_days for i,sid in enumerate(b.ids)])
        out=b.finish("a"*64)
        return b,out["simulation"]

    def test_attribution_reconciles_actual_orders(self):
        b,sim=self.prepared()
        out=r.attribution(b.execution,sim)
        self.assertTrue(out["reconciled"])
        self.assertTrue(sim["orders"])
        self.assertAlmostEqual(out["net_pnl"],sim["curve"][-1]["equity"]-10000)
        self.assertFalse(out["broker_costs_verified"])

    def test_tampered_ledger_rejected(self):
        b,sim=self.prepared()
        for edit in (lambda s:s["orders"][0].update(cost=999),
                     lambda s:s["curve"][-1].update(equity=1),
                     lambda s:s["trades"][0].update(shares=999)):
            bad=deepcopy(sim)
            edit(bad)
            with self.assertRaises(ValueError):
                r.attribution(b.execution,bad)

    def test_replay_review_guard(self):
        b,sim=self.prepared()
        bad=deepcopy(b.execution)
        bad["live_execution_enabled"]=True
        with self.assertRaisesRegex(ValueError,"SYNTHETIC_ONLY"):
            r.review(bad,sim,common_days=b.common_days,sectors=b.sectors)
        with self.assertRaisesRegex(ValueError,"COMMON_WINDOW"):
            r.review(b.execution,sim,common_days=b.common_days[1:],sectors=b.sectors)


class CurveTests(unittest.TestCase):
    def test_parallel_curve_matches_flat_shock(self):
        cf=[(1.,3.),(5.,103.)]
        q=curve_shock(cf,.04,{1:100,5:100})
        self.assertAlmostEqual(q["scenario_value_per_100"],_value_at_yield(.05,cf))
        self.assertAlmostEqual(sum(q["key_rate_dv01_per_100"].values()),q["parallel_dv01_per_100"])
        self.assertFalse(q["observed_curve_verified"])

    def test_nonparallel_and_interpolation(self):
        cf=[(3.,100.)]
        q=curve_shock(cf,.04,{1:-100,5:100})
        self.assertAlmostEqual(q["scenario_pnl_per_100"],0.)
        self.assertAlmostEqual(q["key_rate_dv01_per_100"]["1"],q["key_rate_dv01_per_100"]["5"])

    def test_invalid_curves_block(self):
        for shocks in ({1:0},{0:0,5:1},{1:True,5:1},{1:0,5:float("nan")}):
            with self.assertRaises(ValueError):
                curve_shock([(5.,100.)],.04,shocks)
