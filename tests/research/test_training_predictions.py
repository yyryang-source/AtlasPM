import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from importlib.util import find_spec
from atlaspm.research.training_predictions import PredictionArchive
from atlaspm.research.training_engine import train
from .test_training_engine import fixture


class ArchiveTests(unittest.TestCase):
    def test_unrelated_database_is_not_modified(self):
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                path=Path('local_data/existing.sqlite3')
                path.parent.mkdir()
                with closing(sqlite3.connect(path)) as con,con:
                    con.execute('CREATE TABLE unrelated (value TEXT)')
                before=path.read_bytes()
                with self.assertRaisesRegex(sqlite3.OperationalError,'ALREADY_EXISTS'):
                    PredictionArchive(path)
                self.assertEqual(path.read_bytes(),before)
            finally:
                os.chdir(cwd)

    def test_keyset_pages_release_read_transactions(self):
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                archive=PredictionArchive('local_data/p.sqlite3')
                rows=[dict(date='2020-01-02',security_id=f'SYN:{i:04}',label_end_date='2020-01-03',actual=.1,prediction=.05) for i in range(23)]
                archive.append('ridge',rows)
                stream=archive.rows('ridge',page_size=4)
                first=next(stream)
                archive.append('ensemble',rows)
                with closing(sqlite3.connect(archive.path)) as con:
                    self.assertEqual(con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0],0)
                self.assertEqual([first,*stream],rows)
                self.assertEqual(list(archive.rows('ensemble',page_size=3)),rows)
            finally:
                os.chdir(cwd)

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
                a=train(p,minimum_train_sessions=10,ensemble={"minimum_dates":1})
                b=train(p,minimum_train_sessions=10,
                        prediction_archive_path="local_data/predictions.sqlite3",ensemble={"minimum_dates":1})
                self.assertEqual(a["metrics"],b["metrics"])
                self.assertEqual(a["ensemble_weight_history"],b["ensemble_weight_history"])
                self.assertIsNone(b["predictions"])
                self.assertEqual(b["prediction_storage"],"sqlite")
                with closing(sqlite3.connect(b["prediction_archive_path"])) as con:
                    self.assertEqual(con.execute("SELECT status FROM prediction_state").fetchone(),("COMPLETE",))
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0],
                                     sum(len(rows) for rows in a["predictions"].values()))
            finally:
                os.chdir(cwd)
