import os
import tempfile
import unittest
from pathlib import Path
from importlib.util import find_spec
from atlaspm.research.training_engine import train
from .test_training_engine import fixture


@unittest.skipUnless(find_spec("numpy"),"numpy required")
class LifecycleIntegrationTests(unittest.TestCase):
    def test_disk_lifecycle_matches_memory(self):
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                p=fixture()
                options=dict(minimum_train_sessions=10,incremental_ridge=True,
                    ensemble={"minimum_dates":2},
                    regime_ensemble={"market":{"lookback":3},"weights":{"minimum_dates":2}},
                    monitoring={"window_dates":5,"minimum_dates":3,"coverage":.75})
                a=train(p,**options)
                b=train(p,**options,feature_cache_dir="local_data/cache",
                        prediction_archive_path="local_data/predictions.sqlite3",
                        model_artifact_path="local_data/models.sqlite3",
                        checkpoint_path="local_data/checkpoint.sqlite3")
                self.assertEqual(a["metrics"],b["metrics"])
                self.assertEqual(a["monitoring"],b["monitoring"])
                self.assertEqual(a["regime_weight_history"],b["regime_weight_history"])
                self.assertEqual(set(b["metrics"]),{"ridge","historical_mean","adaptive_ensemble","regime_ensemble"})
                self.assertFalse(b["model_registry_admission"])
                self.assertTrue(all(f["model_artifact_ids"] for f in b["folds"]))
            finally:
                os.chdir(cwd)
