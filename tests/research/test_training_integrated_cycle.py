"""Actual synthetic fitting/inference, risk, OMS, exit and feedback integration."""
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atlaspm.engine.contracts import timestamp
from atlaspm.framework.trained_cycle import run_trained_fixture
from atlaspm.research.training_artifacts import ModelArtifacts, predict_snapshot
from atlaspm.research.training_registry import FLAGS, load

ROOT=Path(__file__).resolve().parents[2]


@unittest.skipUnless(importlib.util.find_spec("numpy") and importlib.util.find_spec("scipy"),"portfolio extra required")
class TrainedCycleTests(unittest.TestCase):
    def setUp(self):
        self.payload=json.loads((ROOT/"examples/unified_portfolio_synthetic.json").read_text())
        root=Path("local_data")
        root.mkdir(exist_ok=True)
        self.tmp=TemporaryDirectory(prefix="trained-cycle-test-",dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"registry.sqlite3"

    def run_cycle(self,**kwargs):
        return run_trained_fixture(self.payload,registry_path=self.db,**kwargs)

    def test_actual_artifacts_drive_central_decision_and_flat_feedback(self):
        result=self.run_cycle()
        self.assertEqual(result["decision"],"SYNTHETIC_TRAINED_EVENT_TO_FEEDBACK_COMPLETE")
        self.assertTrue(all(result[k] is v for k,v in FLAGS.items()))
        self.assertTrue(result["trained_model_used"])
        self.assertFalse(result["model_promoted"])
        models=result["models"]
        self.assertLess(models[1]["historical_window_end"],models[0]["historical_window_start"])
        self.assertNotEqual(models[0]["training_dataset_sha256"],models[1]["training_dataset_sha256"])
        store=ModelArtifacts(self.db)
        for model in models:
            trained=load(self.db,model["training_run_id"])["report"]
            self.assertGreater(len(trained["folds"]),1)
            self.assertTrue(all(f["latest_train_label"]<f["test_start"] for f in trained["folds"]))
            record=store.load(model["artifact_id"])
            stamp=self.payload["as_of"]
            current={"mode":"synthetic","synthetic_fixture":True,"feature_names":["growth","cycle"],
                "sessions":[{"date":stamp[:10],"close_at":stamp,"assets":[{
                    "security_id":"SYN:"+p["symbol"],"source":"synthetic","observed_at":stamp,
                    "available_at":stamp,"close":p["mark_price"],"features":{"growth":.10+.01*i,"cycle":0.}}
                    for i,p in enumerate(self.payload["account"]["positions"])]}]}
            inferred=predict_snapshot(record,current)
            actual={r["security_id"]:r["expected_return"] for r in result["research_rows"]
                    if r["model_version"]=="ridge:"+model["artifact_id"]}
            self.assertEqual(actual,{r["security_id"]:r["prediction"] for r in inferred["predictions"]})
        cycle=result["cycle"]
        self.assertTrue(cycle["attribution"]["reconciled"])
        self.assertTrue(all(cycle["entry"]["portfolio"]["reconciliation_checks"].values()))
        final=cycle["exit_oms"]["account_replay"]
        self.assertFalse(final["positions"])
        self.assertEqual(final["unsettled_receivable"],0)
        self.assertTrue(all(o["status"]=="FILLED" for o in cycle["exit_oms"]["orders"]))
        self.assertEqual(timestamp(result["maturity_at"],"maturity")-timestamp(self.payload["as_of"],"as_of"),timedelta(days=365))
        learning=result["learning"]
        self.assertTrue(learning["retraining_review_required"])
        self.assertFalse(learning["automatic_retraining"])
        self.assertAlmostEqual(learning["matured_portfolio_feedback"]["ending_equity"],final["opening_net_liquidation"])
        for row in learning["prediction_errors"]:
            self.assertAlmostEqual(row["prediction_error"],-.05-row["expected_return"])
        self.assertEqual(load(self.db,result["cycle_id"])["report"]["decision"],result["decision"])

    def test_exact_retry_is_content_addressed_and_feedback_once(self):
        first=self.run_cycle()
        def counts():
            with closing(sqlite3.connect(self.db)) as con:
                return [con.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
                        for table in ("research_runs","model_artifacts","paper_feedback")]
        original=counts()
        second=self.run_cycle()
        self.assertEqual(first["cycle_id"],second["cycle_id"])
        self.assertEqual(original,counts())
        self.assertEqual(original[-1],1)
        with self.assertRaisesRegex(ValueError,"PAPER_FEEDBACK_ALREADY_FINALIZED"):
            self.run_cycle(exit_price_fraction=.9)

    def test_independent_risk_veto_prevents_feedback_completion(self):
        self.payload["constraints"]["max_stress_loss_fraction"]=.01
        result=self.run_cycle()
        self.assertEqual(result["decision"],"TRAINED_CYCLE_ENTRY_BLOCKED")
        self.assertNotIn("learning",result)
        self.assertFalse(result["entry"]["portfolio"]["broker_order_submission_enabled"])

    def test_partial_fills_cannot_claim_flat_or_final_feedback(self):
        self.payload["paper"]["fill_fraction"]=.5
        result=self.run_cycle()
        self.assertEqual(result["decision"],"TRAINED_CYCLE_EXIT_BLOCKED")
        self.assertEqual(result["cycle"]["decision"],"ENTRY_NOT_FULLY_FILLED_OR_SETTLED")
        self.assertNotIn("learning",result)

    def test_real_or_permission_override_rejected_before_persistence(self):
        for field,value in (("mode","real"),("synthetic_fixture",False),("live_execution_enabled",True),
                            ("broker_order_submission_enabled",True),("approved_for_model_training",True),
                            ("approved_for_backtest",True),("external_ai_allowed",True)):
            with self.subTest(field=field):
                payload=deepcopy(self.payload)
                payload[field]=value
                with self.assertRaisesRegex(ValueError,"SYNTHETIC_SCOPE"):
                    run_trained_fixture(payload,registry_path=self.db)
                self.assertFalse(self.db.exists())

    def test_inference_rejection_has_no_fixed_positive_fallback(self):
        original=predict_snapshot
        def adverse(*args,**kwargs):
            result=original(*args,**kwargs)
            for row in result["predictions"]:
                row["prediction"]=-.1
            return result
        with patch("atlaspm.framework.trained_cycle.predict_snapshot",side_effect=adverse):
            result=self.run_cycle()
        self.assertEqual(result["decision"],"TRAINED_CYCLE_ENTRY_BLOCKED")
        self.assertEqual(result["entry"]["reason"],"NO_COMPLETE_POSITIVE_HELD_EQUITY_SET")
        self.assertNotIn("learning",result)

    def test_artifact_corruption_is_not_retrained_over_or_accepted(self):
        result=self.run_cycle()
        artifact=result["models"][0]["artifact_id"]
        with closing(sqlite3.connect(self.db)) as con,con:
            con.execute("UPDATE model_artifacts SET payload='{}' WHERE artifact_id=?",(artifact,))
        with self.assertRaisesRegex(ValueError,"ARTIFACT_INTEGRITY_FAILURE"):
            self.run_cycle()
