import unittest
from importlib.util import find_spec
from unittest.mock import patch

from atlaspm.research import training_autonomy as a
from atlaspm.research.training_models import validate_parameters
from .test_training_engine import fixture


CONFIG={"minimum_train_sessions":10,"test_sessions":5}
SPACES=[{"model":"historical_mean","grid":{}},
        {"model":"ridge","grid":{"ridge_alpha":[.1,1.,10.],
                                "features":[["momentum"],["momentum","variation"]]}}]


class SearchTests(unittest.TestCase):
    def test_balanced_reproducible_budget(self):
        one=a.search_candidates(SPACES,CONFIG,budget=4,seed=7)
        self.assertEqual(one,a.search_candidates(SPACES,CONFIG,budget=4,seed=7))
        self.assertEqual(len(one),4)
        self.assertEqual({x["model"] for x in one},{"historical_mean","ridge"})
        self.assertEqual(len({a._sha(x) for x in one}),4)

    def test_unsafe_parameter_and_grid_rejected(self):
        for params in ({"n_jobs":8},{"max_depth":100},{"n_estimators":True}):
            with self.assertRaises(ValueError):
                validate_parameters("random_forest",params)
        self.assertEqual(validate_parameters("random_forest",{"max_depth":4}),{"max_depth":4})
        with self.assertRaisesRegex(ValueError,"SEARCH_GRID_LIMIT"):
            a.search_candidates([{"model":"ridge","grid":{"ridge_alpha":[1]*4097}}],CONFIG)
        with self.assertRaises(ValueError):
            a.search_candidates([{"model":"ridge","grid":{"unknown":[1,2]}}],CONFIG)

    def test_retrain_review_patience_and_cooldown(self):
        rows=[{"date":f"2020-02-{i:02}","models":{"ridge":{"drift_alert":True,
            "recent_mae":.3,"reference_mae":.1},"historical_mean":{"drift_alert":True}}}
            for i in range(1,11)]
        out=a.review_schedule({"daily":rows,"baseline":"historical_mean"},
                             holdout_start="2020-02-03",patience=2,cooldown_dates=4)
        self.assertEqual([r["date"] for r in out],["2020-02-04","2020-02-08"])
        self.assertTrue(all(r["status"]=="RETRAIN_REVIEW_REQUIRED" for r in out))

    def test_real_data_blocked_before_selection(self):
        with patch.object(a,"tournament") as select:
            with self.assertRaisesRegex(ValueError,"REAL_TRAINING_DATA_NOT_APPROVED"):
                a.run({**fixture(),"mode":"real_unverified_research"},config=CONFIG,
                      spaces=SPACES,holdout_start=31)
            select.assert_not_called()


@unittest.skipUnless(find_spec("numpy"),"numpy extra required")
class LifecycleTests(unittest.TestCase):
    def test_complete_pipeline_and_no_double_full_fit(self):
        p=fixture()
        opts=dict(config=CONFIG,spaces=SPACES,holdout_start=31,budget=4,search_seed=7,
                  ensemble={"window_dates":3,"minimum_dates":2},
                  regime_ensemble={"market":{"lookback":3},"weights":{"window_dates":3,"minimum_dates":2}},
                  monitoring={"window_dates":3,"minimum_dates":2})
        with patch.object(a,"train",wraps=a.train) as fit:
            r=a.run(p,**opts)
        self.assertEqual(fit.call_count,1)
        self.assertEqual(r["selection"]["selected_model"],"ridge")
        self.assertIn("adaptive_ensemble",r["holdout_metrics"])
        self.assertIn("regime_ensemble",r["holdout_metrics"])
        self.assertGreater(r["scheduled_walk_forward_refits"],0)
        self.assertFalse(r["approved_for_model_training"])
        self.assertFalse(r["automatic_retrain_on_drift_enabled"])
        self.assertFalse(r["model_registry_admission"])
        self.assertFalse(r["live_execution_enabled"])
        for s in p["sessions"][31:]:
            for asset in s["assets"]:
                asset["close"]*=2
        after=a.run(p,**opts)
        self.assertEqual(r["selection"]["trials"],after["selection"]["trials"])
        self.assertEqual(r["selection"]["selected_features"],after["selection"]["selected_features"])

    @unittest.skipUnless(find_spec("sklearn"),"research-ml extra required")
    def test_real_estimator_parameter_propagation(self):
        from atlaspm.research.training_engine import train
        r=train(fixture(),minimum_train_sessions=10,models=["random_forest"],
                model_parameters={"random_forest":{"n_estimators":8,"max_depth":3}})
        self.assertEqual(r["model_settings"]["random_forest"]["n_estimators"],8)
        self.assertEqual(r["model_settings"]["random_forest"]["max_depth"],3)
        self.assertFalse(r["live_execution_enabled"])
