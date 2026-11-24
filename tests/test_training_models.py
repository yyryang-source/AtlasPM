import unittest
from importlib.util import find_spec
from atlaspm.research.training_models import MODELS,PACKAGES
from atlaspm.research.training_engine import train
from test_training_engine import fixture


@unittest.skipUnless(find_spec("numpy"),"numpy extra required")
class ModelSelectionTests(unittest.TestCase):
    def test_unknown_model(self):
        with self.assertRaisesRegex(ValueError,"INVALID_MODEL_SELECTION"):
            train(fixture(),models=["unknown"])

    def test_baseline_subset(self):
        out=train(fixture(),minimum_train_sessions=10,models=["historical_mean"])
        self.assertEqual(set(out["metrics"]),{"historical_mean"})


@unittest.skipUnless(all(find_spec(m) for m in ("numpy","sklearn","lightgbm","xgboost","catboost")),
                     "research-ml extra required; dedicated CI installs it")
class AllModelsTests(unittest.TestCase):
    def test_aligned_predictions(self):
        out=train(fixture(),minimum_train_sessions=10,test_sessions=10,models=list(MODELS))
        self.assertEqual(set(out["metrics"]),set(MODELS))
        reference=[(p["security_id"],p["date"],p["actual"]) for p in out["predictions"]["ridge"]]
        for name in MODELS:
            self.assertEqual(reference,[(p["security_id"],p["date"],p["actual"]) for p in out["predictions"][name]])
            self.assertGreaterEqual(out["metrics"][name]["mae"],0)
        self.assertFalse(out["model_registry_admission"])
        self.assertFalse(out["live_execution_enabled"])

    def test_future_data_does_not_change_first_fold(self):
        p=fixture()
        models=list(MODELS)
        a=train(p,minimum_train_sessions=10,test_sessions=10,models=models)
        for row in p["sessions"][35:]:
            for asset in row["assets"]:
                asset["close"]*=2
        b=train(p,minimum_train_sessions=10,test_sessions=10,models=models)
        for name in models:
            self.assertEqual(a["predictions"][name][:40],b["predictions"][name][:40])
