"""Execution-lag labels share the same purge and streaming contracts."""
from copy import deepcopy
from importlib.util import find_spec
import unittest

from atlaspm.research.training_dataset import build_panel,purged_folds
from atlaspm.research.training_compact import feature_workspace
from atlaspm.research.training_stream_panel import build_stream_panel
from atlaspm.research.training_engine import train
from atlaspm.research.training_adaptive import adaptive_research
from research.test_training_autonomous import fixture,local_workspace


class LaggedPanelTests(unittest.TestCase):
    def test_label_uses_next_close_and_full_maturity_span(self):
        p=fixture(12)
        p["sessions"][1]["assets"][0]["close"]=200.
        p["sessions"][2]["assets"][0]["close"]=220.
        a=build_panel(p,1)
        b=build_panel(p,1,entry_lag_sessions=1)
        self.assertAlmostEqual(a["samples"][0]["y"],1.)
        self.assertAlmostEqual(b["samples"][0]["y"],.1)
        self.assertEqual(b["samples"][0]["label_end_date"],p["sessions"][2]["date"])
        self.assertEqual(b["unmatured_final_sessions"],2)
        with self.assertRaisesRegex(ValueError,"INVALID_ENTRY_LAG_SESSIONS"):
            build_panel(p,1,entry_lag_sessions=True)
        with self.assertRaisesRegex(ValueError,"INVALID_AS_OF_NEXT_SESSION"):
            build_panel(p,1,entry_lag_sessions=1,next_session="2021-01-01")

    @unittest.skipUnless(find_spec("numpy"),"portfolio extra required")
    def test_streaming_and_compact_labels_and_purge_agree(self):
        p=fixture(45)
        opts=dict(minimum_train_sessions=10,test_sessions=5,embargo_sessions=1)
        memory=build_panel(p,1,compact=True,entry_lag_sessions=1)
        with local_workspace():
            with feature_workspace("local_data/feature-cache") as column:
                disk=build_stream_panel(p,iter(p["sessions"]),1,1,column,entry_lag_sessions=1)
                self.assertEqual(list(memory["samples"]),list(disk["samples"]))
                self.assertEqual(purged_folds(memory,**opts),purged_folds(disk,**opts))
        for fold in purged_folds(memory,**opts):
            self.assertLess(fold["latest_training_label_end"],fold["test_start_index"]-1)


@unittest.skipUnless(find_spec("numpy"),"portfolio extra required")
class LaggedTrainingTests(unittest.TestCase):
    def test_future_marks_do_not_change_prior_predictions(self):
        p=fixture(65)
        options=dict(horizon=1,entry_lag_sessions=1,minimum_train_sessions=10)
        a=train(p,**options)
        b=deepcopy(p)
        for session in b["sessions"][40:]:
            for row in session["assets"]:
                row["close"]*=2.
        b=train(b,**options)
        cutoff=p["sessions"][40]["date"]
        for model in a["predictions"]:
            self.assertEqual([r for r in a["predictions"][model] if r["label_end_date"]<cutoff],
                             [r for r in b["predictions"][model] if r["label_end_date"]<cutoff])
        self.assertEqual(a["training_config"]["entry_lag_sessions"],1)
        self.assertEqual(a["entry_lag_sessions"],1)
        self.assertFalse(a["live_execution_enabled"])

    def test_adaptive_profiles_use_execution_aligned_metrics(self):
        p=fixture()
        with local_workspace() as path:
            a=adaptive_research(p,registry_path=path,holdout_start=62,
                momentum_feature="momentum",minimum_train_sessions=10,
                execution_aligned_labels=True)
            self.assertIn("adaptive",a["holdout"])
            self.assertFalse(a["automatic_promotion_enabled"])
            self.assertFalse(a["approved_for_model_training"])
