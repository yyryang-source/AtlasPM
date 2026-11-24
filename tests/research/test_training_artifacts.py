import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path
from atlaspm.research.training_artifacts import ModelArtifacts,predict_snapshot
from atlaspm.research.training_engine import train
from .test_training_engine import fixture


@unittest.skipUnless(find_spec("numpy"),"numpy required")
class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.cwd=Path.cwd()
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir,self.cwd)
        os.chdir(self.tmp.name)
        self.path="local_data/models.sqlite3"
        self.payload=fixture()

    def verify_model(self,models,length):
        out=train(self.payload,minimum_train_sessions=10,test_sessions=20,models=models,
                  sequence_length=length,sequence_epochs=2,model_artifact_path=self.path)
        store=ModelArtifacts(self.path)
        first=out["folds"][0]
        day=first["test_start"]
        i=next(i for i,r in enumerate(self.payload["sessions"]) if r["date"]==day)
        for name,key in first["model_artifact_ids"].items():
            record=store.load(key)
            n=record["sequence_length"]
            window={**self.payload,"sessions":self.payload["sessions"][i-n+1:i+1]}
            replay=predict_snapshot(record,window)
            expected={r["security_id"]:r["prediction"] for r in out["predictions"][name] if r["date"]==day}
            self.assertFalse(replay["live_execution_enabled"])
            self.assertFalse(record["model_registry_admission"])
            self.assertEqual(set(expected),{r["security_id"] for r in replay["predictions"]})
            for row in replay["predictions"]:
                self.assertAlmostEqual(row["prediction"],expected[row["security_id"]],places=6)
        return out,store

    def test_tabular_snapshot_preserves_panel_window(self):
        self.verify_model(['ridge','historical_mean'],3)

    def test_linear_roundtrip_and_tamper(self):
        out,store=self.verify_model(["ridge","historical_mean"],1)
        key=out["folds"][0]["model_artifact_ids"]["ridge"]
        record=store.load(key)
        with self.assertRaisesRegex(ValueError,"ARTIFACT_LOOKAHEAD"):
            predict_snapshot(record,{**self.payload,"sessions":self.payload["sessions"][:1]})
        with closing(sqlite3.connect(store.path)) as con,con:
            con.execute("UPDATE model_artifacts SET payload='{}' WHERE artifact_id=?",(key,))
        with self.assertRaisesRegex(ValueError,"ARTIFACT_INTEGRITY_FAILURE"):
            store.load(key)

    def test_resume_artifact_references(self):
        options=dict(minimum_train_sessions=10,models=["ridge"],model_artifact_path=self.path,
                     checkpoint_path="local_data/checkpoints.sqlite3")
        a=train(self.payload,**options)
        b=train(self.payload,**options)
        self.assertEqual(a["folds"],b["folds"])
        self.assertEqual(b["resumed_folds"],len(a["folds"]))

    @unittest.skipUnless(all(find_spec(m) for m in ("sklearn","lightgbm","xgboost","catboost")),"research-ml required")
    def test_optional_model_roundtrip(self):
        self.verify_model(["elastic_net","random_forest","mlp","lightgbm","xgboost","catboost"],1)

    @unittest.skipUnless(find_spec("torch"),"torch required")
    def test_sequence_roundtrip(self):
        self.verify_model(["lstm","gru","transformer"],3)
