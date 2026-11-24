import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from importlib.util import find_spec
from atlaspm.research.training_predictions import PredictionArchive
from atlaspm.research.training_engine import train
from test_training_engine import fixture


class ArchiveTests(unittest.TestCase):
    def test_lifecycle(self):
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                archive=PredictionArchive("local_data/predictions.sqlite3")
                row=dict(date="2020-01-02",security_id="SYN:A",label_end_date="2020-01-03",
                         actual=None,prediction=.01)
                archive.append("ridge",[row])
                self.assertEqual(list(archive.rows("ridge")),[row])
                with self.assertRaises(sqlite3.IntegrityError):
                    archive.append("ridge",[row])
                archive.finalize()
                with self.assertRaises(ValueError):
                    archive.append("ridge",[row])
                with self.assertRaises(sqlite3.OperationalError):
                    PredictionArchive("local_data/predictions.sqlite3")
            finally:
                os.chdir(cwd)


@unittest.skipUnless(find_spec("numpy"),"numpy required")
class ArchiveTrainingTests(unittest.TestCase):
    def test_disk_and_memory_metrics(self):
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                p=fixture()
                a=train(p,minimum_train_sessions=10)
                b=train(p,minimum_train_sessions=10,
                        prediction_archive_path="local_data/predictions.sqlite3")
                self.assertEqual(a["metrics"],b["metrics"])
                self.assertIsNone(b["predictions"])
                self.assertEqual(b["prediction_storage"],"sqlite")
                with sqlite3.connect(b["prediction_archive_path"]) as con:
                    self.assertEqual(con.execute("SELECT status FROM prediction_state").fetchone(),("COMPLETE",))
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0],
                                     sum(len(rows) for rows in a["predictions"].values()))
            finally:
                os.chdir(cwd)
