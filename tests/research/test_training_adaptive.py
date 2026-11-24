"""Adaptive learning contracts: no future labels or implicit GPU preference."""
from copy import deepcopy
from datetime import date,timedelta
from importlib.util import find_spec
import unittest

from atlaspm.research.training_adaptive import adaptive_research,sequence_resources
from atlaspm.research.training_dataset import build_panel,purged_folds
from atlaspm.research.training_engine import train
from atlaspm.research.training_ensemble import adaptive_ensemble
from research.test_training_autonomous import fixture,local_workspace


def forecasts():
    streams={"reference":[],"candidate":[]}
    for i in range(45):
        day=(date(2021,1,1)+timedelta(days=i)).isoformat()
        end=(date(2021,1,1)+timedelta(days=i+1)).isoformat()
        for model,prediction in (("reference",.02),("candidate",.001 if i<20 else .1)):
            streams[model].append(dict(date=day,security_id="SYN:AAA",
                label_end_date=end,actual=0.,prediction=prediction))
    return streams


class AdaptiveWeightTests(unittest.TestCase):
    def test_maturity_and_quarantine(self):
        out=adaptive_ensemble(forecasts(),window_dates=8,minimum_dates=4,
            temperature=.01,reference_model="reference",drift_ratio=1.5,minimum_improvement=.05)
        audit=out["weight_history"]
        self.assertEqual(audit[0]["weights"],{"reference":1.,"candidate":0.})
        self.assertGreater(audit[15]["weights"]["candidate"],.5)
        self.assertIn("candidate",audit[35]["quarantined"])
        self.assertTrue(audit[35]["fallback_to_reference"])

    def test_unmatured_labels_do_not_change_weights(self):
        rows=forecasts()
        kwargs=dict(window_dates=8,minimum_dates=4,temperature=.01,
                    reference_model="reference",drift_ratio=1.5)
        original=adaptive_ensemble(rows,**kwargs)
        altered=deepcopy(rows)
        cutoff=rows["reference"][15]["date"]
        for stream in altered.values():
            for row in stream:
                if row["label_end_date"]>=cutoff:
                    row["actual"]=.8
        second=adaptive_ensemble(altered,**kwargs)
        self.assertEqual(original["weight_history"][:16],second["weight_history"][:16])
        self.assertEqual([r["prediction"] for r in original["predictions"][:16]],
                         [r["prediction"] for r in second["predictions"][:16]])

    def test_cpu_default_and_bounded_input_batch(self):
        result=sequence_resources(batch_size=8192,sequence_length=21,
            feature_count=128,batch_input_budget_mib=1)
        self.assertEqual(result["device"],"cpu")
        self.assertLess(result["batch_size"],8192)
        self.assertLessEqual(result["maximum_float32_input_bytes"],1024**2)
        self.assertFalse(result["total_vram_budget_guaranteed"])
        with self.assertRaisesRegex(ValueError,"ONE_SEQUENCE_EXCEEDS"):
            sequence_resources(sequence_length=252,feature_count=100000,batch_input_budget_mib=1)


@unittest.skipUnless(find_spec("numpy"),"portfolio extra required")
class AdaptiveResearchTests(unittest.TestCase):
    def test_rolling_folds_match_compact_and_preserve_embargo(self):
        p=fixture(65)
        opts=dict(minimum_train_sessions=10,test_sessions=5,embargo_sessions=2,training_window_sessions=12)
        plain=build_panel(p,1)
        compact=build_panel(p,1,compact=True)
        a=purged_folds(plain,**opts)
        b=purged_folds(compact,**opts)
        self.assertEqual(a,b)
        for f in a:
            self.assertLessEqual(len(f["train"]),24)
            self.assertLess(f["latest_training_label_end"],f["test_start_index"]-2)
        result=train(p,horizon=1,**opts)
        self.assertEqual(result["training_config"]["training_window_sessions"],12)
        self.assertLessEqual(max(f["training_rows"] for f in result["folds"]),24)
        with self.assertRaisesRegex(ValueError,"ROLLING_WINDOW_REQUIRES_FULL_REFIT"):
            train(p,horizon=1,incremental_ridge=True,**opts)

    def test_predeclared_research_and_holdout_consumption(self):
        panel=fixture()
        with local_workspace() as path:
            result=adaptive_research(panel,registry_path=path,holdout_start=62,
                momentum_feature="momentum",minimum_train_sessions=10)
            self.assertEqual(result["status"],"ADAPTIVE_ADVANTAGE_NOT_ESTABLISHED")
            self.assertIn("adaptive",result["holdout"])
            self.assertIn("ridge_short",result["profile_audit"])
            self.assertEqual(result["resource_plan"]["device"],"cpu")
            self.assertFalse(result["live_execution_enabled"])
            self.assertFalse(result["model_registry_admission"])
            with self.assertRaisesRegex(ValueError,"HOLDOUT_ALREADY"):
                adaptive_research(panel,registry_path=path,holdout_start=62,
                    momentum_feature="momentum",minimum_train_sessions=10)

    def test_holdout_changes_cannot_change_validation_or_earlier_weights(self):
        p=fixture()
        def run(panel,path):
            return adaptive_research(panel,registry_path=path,holdout_start=62,
                 momentum_feature="momentum",minimum_train_sessions=10)
        with local_workspace() as path:
            first=run(p,path)
        changed=deepcopy(p)
        for s in changed["sessions"][62:]:
            for a in s["assets"]:
                a["close"]*=.7
        with local_workspace() as path:
            second=run(changed,path)
        self.assertEqual(first["validation"],second["validation"])
        cutoff=p["sessions"][62]["date"]
        self.assertEqual([r for r in first["weight_history"] if r["date"]<=cutoff],
                         [r for r in second["weight_history"] if r["date"]<=cutoff])
