import unittest
from importlib.util import find_spec
from atlaspm.research.training_experiment import experiment
from .test_training_engine import fixture


@unittest.skipUnless(find_spec("numpy"),"numpy extra required")
class ExperimentTests(unittest.TestCase):
    def test_holdout_isolation(self):
        p=fixture()
        kwargs=dict(config={"minimum_train_sessions":10,"test_sessions":5},
                    grid={"ridge_alpha":[.1,1.,10.]},holdout_start=31)
        a=experiment(p,**kwargs)
        for row in p["sessions"][31:]:
            for asset in row["assets"]:
                asset["close"]*=2
        b=experiment(p,**kwargs)
        self.assertEqual(a["trials"],b["trials"])
        self.assertEqual(a["selected_parameters"],b["selected_parameters"])
        self.assertFalse(a["approved_for_model_training"])
        self.assertTrue(all(r["date"]>=a["holdout_start"] for r in a["holdout_predictions"]))

    def test_trial_cap(self):
        with self.assertRaisesRegex(ValueError,"EXPERIMENT_TRIAL_LIMIT"):
            experiment(fixture(),config={},grid={"ridge_alpha":list(range(33))},holdout_start=31)

    def test_invalid_grid(self):
        with self.assertRaises(ValueError):
            experiment(fixture(),config={},grid={"horizon":[1,5]},holdout_start=31)

    def test_invalid_holdout_rejected_before_training(self):
        from unittest.mock import patch
        with patch("atlaspm.research.training_experiment.train") as training:
            with self.assertRaisesRegex(ValueError,"HOLDOUT_MUST_START_ON_REFIT_BOUNDARY"):
                experiment(fixture(),config={"minimum_train_sessions":10},grid={"ridge_alpha":[1.]},holdout_start=32)
            training.assert_not_called()

    def test_archive_config_rejected(self):
        with self.assertRaisesRegex(ValueError,"EXPERIMENT_REQUIRES_MEMORY_PREDICTIONS"):
            experiment(fixture(),config={"prediction_archive_path":"local_data/p.sqlite3"},grid={"ridge_alpha":[1.]},holdout_start=31)
