import hashlib
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from importlib.util import find_spec
from atlaspm.research.training_dataset import build_panel,sequence_for,purged_folds
from atlaspm.research.training_compact import feature_workspace
from atlaspm.research.training_stream_panel import build_stream_panel,DiskObservations
from atlaspm.research.training_engine import train,train_parquet
from .test_training_engine import fixture


@unittest.skipUnless(find_spec("numpy"),"numpy required")
class StreamPanelTests(unittest.TestCase):
    def test_stream_panel_equivalence(self):
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                p=fixture()
                del p["sessions"][22]["assets"][0]
                for horizon,length in [(1,1),(5,3),(10,7),(21,2)]:
                    ref=build_panel(p,horizon,length)
                    with feature_workspace("local_data/cache") as col:
                        with closing(DiskObservations(col.path.parent/"obs.sqlite3")) as obs:
                            out=build_stream_panel(p,iter(p["sessions"]),horizon,length,col,obs)
                            self.assertEqual(len(ref["prediction_samples"]),len(out["prediction_samples"]))
                            self.assertEqual(ref["missing_label_paths"],out["missing_label_paths"])
                            self.assertEqual(ref["incomplete_sequences"],out["incomplete_sequences"])
                            self.assertLessEqual(out["peak_buffered_sessions"],horizon+length)
                            for a,b in zip(ref["prediction_samples"],out["prediction_samples"]):
                                self.assertEqual(a,{**b,"sequence":sequence_for(out,b)})
                self.assertEqual(list(Path("local_data/cache").iterdir()),[])
            finally:
                os.chdir(cwd)


@unittest.skipUnless(find_spec("numpy") and find_spec("pyarrow"),"research-io required")
class ParquetTrainingTests(unittest.TestCase):
    def fixture_files(self,root,p):
        import pyarrow as pa
        import pyarrow.parquet as pq
        parts=[]
        for k in range(0,len(p["sessions"]),9):
            rows=[]
            for session in p["sessions"][k:k+9]:
                for asset in session["assets"]:
                    rows.append(dict(date=session["date"],close_at=session["close_at"],
                        **{key:v for key,v in asset.items() if key!="features"},**asset["features"]))
            path=root/f"part-{k:04}.parquet"
            pq.write_table(pa.Table.from_pylist(rows),path,row_group_size=5)
            parts.append(dict(path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        return dict(mode="synthetic",synthetic_fixture=True,feature_names=p["feature_names"],partitions=parts)

    def test_end_to_end(self):
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                p=fixture()
                del p["sessions"][22]["assets"][0]
                root=Path("local_data/input")
                root.mkdir(parents=True)
                manifest=self.fixture_files(root,p)
                settings=dict(minimum_train_sessions=10,sequence_length=3,ensemble={"minimum_dates":1})
                a=train(p,**settings)
                b=train_parquet(root,manifest,feature_cache_dir="local_data/cache",batch_rows=3,
                                prediction_archive_path="local_data/results.sqlite3",**settings)
                self.assertEqual(a["metrics"],b["metrics"])
                self.assertEqual(a["ensemble_weight_history"],b["ensemble_weight_history"])
                self.assertEqual(b["input_storage"],"parquet_stream")
                self.assertLessEqual(b["peak_buffered_sessions"],8)
                self.assertIsNone(b["predictions"])
                self.assertEqual(list(Path("local_data/cache").iterdir()),[])
            finally:
                os.chdir(cwd)
