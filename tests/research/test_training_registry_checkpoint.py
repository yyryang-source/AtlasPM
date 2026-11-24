import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from importlib.util import find_spec
from atlaspm.research.training_registry import register,load,list_runs,FLAGS
from atlaspm.research.training_engine import train
from .test_training_engine import fixture


class RegistryTests(unittest.TestCase):
    def test_immutable_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"runs.sqlite3"
            report=dict(FLAGS,dataset_sha256="a"*64,metrics={"mae":.1})
            a=register(path,{"seed":42},report)
            self.assertEqual(a,register(path,{"seed":42},report))
            b=register(path,{"seed":43},report)
            self.assertNotEqual(a,b)
            self.assertEqual(len(list_runs(path)),2)
            self.assertEqual(load(path,a)["report"],report)

    def test_permission_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            report=dict(FLAGS,dataset_sha256="a"*64,live_execution_enabled=True)
            with self.assertRaises(ValueError):
                register(Path(tmp)/"runs.sqlite3",{},report)

    def test_tamper_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"runs.sqlite3"
            report=dict(FLAGS,dataset_sha256="a"*64)
            a=register(path,{},report)
            with closing(sqlite3.connect(path)) as con, con:
                con.execute("UPDATE research_runs SET config_json=?",('{"changed":true}',))
            with self.assertRaisesRegex(ValueError,"REGISTRY_INTEGRITY_FAILURE"):
                load(path,a)


@unittest.skipUnless(find_spec("numpy"),"numpy extra required")
class CheckpointTests(unittest.TestCase):
    def test_resume_and_config_isolation(self):
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                path="local_data/folds.sqlite3"
                p=fixture()
                a=train(p,minimum_train_sessions=10,checkpoint_path=path)
                b=train(p,minimum_train_sessions=10,checkpoint_path=path)
                self.assertEqual(a["predictions"],b["predictions"])
                self.assertEqual(a["folds"],b["folds"])
                self.assertEqual(b["resumed_folds"],len(a["folds"]))
                c=train(p,minimum_train_sessions=10,ridge_alpha=2,checkpoint_path=path)
                self.assertNotEqual(a["checkpoint_run_id"],c["checkpoint_run_id"])
                self.assertEqual(c["resumed_folds"],0)
            finally:
                os.chdir(cwd)
