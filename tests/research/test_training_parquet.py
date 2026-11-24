import hashlib
import tempfile
import unittest
from pathlib import Path
from importlib.util import find_spec
from atlaspm.research.training_parquet import iter_sessions


@unittest.skipUnless(find_spec("pyarrow"),"research-io extra required")
class ParquetTests(unittest.TestCase):
    def partition(self,root,name,rows):
        import pyarrow as pa
        import pyarrow.parquet as pq
        path=root/name
        pq.write_table(pa.Table.from_pylist(rows),path)
        return dict(path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def row(self,day,sid):
        t=day+"T16:00:00-05:00"
        return dict(date=day,close_at=t,security_id=sid,source="synthetic",
                    observed_at=t,available_at=t,close=100.,momentum=.1)

    def manifest(self,parts):
        return dict(mode="synthetic",synthetic_fixture=True,feature_names=["momentum"],partitions=parts)

    def test_split_session_and_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a=self.partition(root,"a.parquet",[self.row("2020-01-02","SYN:A")])
            b=self.partition(root,"b.parquet",[self.row("2020-01-02","SYN:B"),self.row("2020-01-03","SYN:A")])
            out=list(iter_sessions(root,self.manifest([a,b]),batch_rows=1))
            self.assertEqual([len(r["assets"]) for r in out],[2,1])
            self.assertEqual(out[0]["assets"][1]["features"],{"momentum":.1})

    def test_corrupt_partition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a=self.partition(root,"a.parquet",[self.row("2020-01-02","SYN:A")])
            a["sha256"]="0"*64
            with self.assertRaisesRegex(ValueError,"PARQUET_INTEGRITY_FAILURE"):
                list(iter_sessions(root,self.manifest([a])))

    def test_duplicate_security(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            row=self.row("2020-01-02","SYN:A")
            a=self.partition(root,"a.parquet",[row,row])
            with self.assertRaisesRegex(ValueError,"INVALID_PARQUET_SECURITY"):
                list(iter_sessions(root,self.manifest([a]),batch_rows=1))

    def test_future_availability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            row=self.row("2020-01-02","SYN:A")
            row["available_at"]="2020-01-03T16:00:00-05:00"
            a=self.partition(root,"a.parquet",[row])
            with self.assertRaisesRegex(ValueError,"FEATURE_LOOKAHEAD"):
                list(iter_sessions(root,self.manifest([a])))

    def test_out_of_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a=self.partition(root,"a.parquet",[self.row("2020-01-03","SYN:A"),self.row("2020-01-02","SYN:A")])
            with self.assertRaisesRegex(ValueError,"PARQUET_DATE_ORDER"):
                list(iter_sessions(root,self.manifest([a])))

    def test_real_data_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=self.manifest([])
            m["mode"]="historical"
            with self.assertRaisesRegex(ValueError,"REAL_PARQUET_NOT_APPROVED"):
                list(iter_sessions(tmp,m))
