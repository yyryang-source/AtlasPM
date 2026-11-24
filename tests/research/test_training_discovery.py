"""Execution-aligned discovery and one-use holdout regression."""
from copy import deepcopy
import unittest
from atlaspm.research.training_discovery import discover,_observations,_rows,_score_day,_redundancy
from atlaspm.research.training_strategy_metrics import evaluate
from research.test_training_autonomous import fixture,local_workspace


def options(path):
    return dict(registry_path=path,holdout_start=62,momentum_feature="momentum",
                budget=4,minimum_symbols=2,minimum_periods=20,cost_bps=0.,
                bootstrap_draws=1000)


class DiscoveryTests(unittest.TestCase):
    def test_labels_match_execution_not_signal_close(self):
        p=fixture(12)
        features,observed=_observations(p,max_rows=1000)
        rule=dict(op="rank",features=["predictive"],sign=1)
        rows=_rows(observed,features,rule,0,len(observed),minimum_symbols=2)
        self.assertEqual(rows[0]["label_end_date"],p["sessions"][2]["date"])
        self.assertAlmostEqual(rows[0]["actual"],.001,places=12)
        result=evaluate(rows,p["sessions"],execution_aligned_labels=True,cost_bps=0.)
        self.assertEqual(result["daily"][0]["entry_date"],p["sessions"][1]["date"])
        bad=deepcopy(rows)
        bad[0]["actual"]+=.01
        with self.assertRaisesRegex(ValueError,"EXECUTION_LABEL_RETURN_MISMATCH"):
            evaluate(bad,p["sessions"],execution_aligned_labels=True)
        bad=deepcopy(rows)
        bad[0]["label_end_date"]=p["sessions"][1]["date"]
        with self.assertRaisesRegex(ValueError,"STRATEGY_LABEL_MATURITY_MISMATCH"):
            evaluate(bad,p["sessions"],execution_aligned_labels=True)

    def test_frozen_rule_and_holdout_consumption(self):
        p=fixture()
        with local_workspace() as path:
            out=discover(p,**options(path))
            self.assertEqual(out["status"],"DISCOVERY_HOLDOUT_PASS_REVIEW_REQUIRED")
            self.assertEqual(out["selected_rule"],dict(op="rank",features=["momentum"],sign=-1))
            self.assertTrue(all(v["lower_bound"]>0 for v in out["holdout_comparison"].values()))
            self.assertTrue(any("REDUNDANT" in r for t in out["validation"]["trials"] for r in t["reasons"]))
            self.assertFalse(out["approved_for_model_training"])
            self.assertFalse(out["live_execution_enabled"])
            self.assertFalse(out["automatic_promotion_enabled"])
            with self.assertRaisesRegex(ValueError,"HOLDOUT_ALREADY"):
                discover(p,**options(path))

    def test_holdout_mutation_cannot_change_validation(self):
        p=fixture()
        with local_workspace() as path:
            a=discover(p,**options(path))
        for s in p["sessions"][62:]:
            for asset in s["assets"]:
                asset["close"]*=.8
        with local_workspace() as path:
            b=discover(p,**options(path))
        self.assertEqual(a["validation"],b["validation"])

    def test_no_fake_security_or_permission_or_future_feature(self):
        p=fixture()
        for key,value,error in (("mode","historical","REAL_TRAINING_DATA_NOT_APPROVED"),
                                ("live_execution_enabled",True,"PERMISSION_OVERRIDE")):
            with local_workspace() as path:
                with self.assertRaisesRegex(ValueError,error):
                    discover({**p,key:value},**options(path))
        p["sessions"][0]["assets"][0]["available_at"]=p["sessions"][1]["close_at"]
        with local_workspace() as path:
            with self.assertRaises(ValueError):
                discover(p,**options(path))

    def test_missing_execution_path_refuses_survivorship(self):
        p=fixture()
        p["sessions"][4]["assets"].pop()
        with local_workspace() as path:
            with self.assertRaisesRegex(ValueError,"MISSING_EXECUTABLE_PATH"):
                discover(p,**options(path))

    def test_ties_and_rank_invariance(self):
        p=fixture(12)
        features,observed=_observations(p,max_rows=1000)
        rule=dict(op="rank",features=["predictive"],sign=1)
        scores=_score_day(observed[0][1],features,rule)
        self.assertEqual(scores,{"SYN:AAA":-1.,"SYN:BBB":1.})
        a=_rows(observed,features,rule,0,12,minimum_symbols=2)
        b=_rows(observed,features,{**rule,"sign":-1},0,12,minimum_symbols=2)
        self.assertAlmostEqual(_redundancy(a,b),1.)
        for item in p["sessions"][0]["assets"]:
            item["features"]["predictive"]=1.
        _,observed=_observations(p,max_rows=1000)
        self.assertEqual(set(_score_day(observed[0][1],features,rule).values()),{0.})
