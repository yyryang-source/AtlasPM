"""Local partition byte recovery tests, not market certification."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import unittest

from atlaspm.data import lake_recovery as lake
from atlaspm.research.training_registry import digest


class LakeRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        prior=Path.cwd()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir,prior)
        self.months=["2020-01"]
        self.scopes=["raw","rth"]
        self.required,self.optional=lake._plan(self.months,self.scopes)
        for p in self.required:
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(b"partition fixture")
        self.optional[0].parent.mkdir(parents=True,exist_ok=True)
        self.optional[0].write_bytes(b"quality fixture")
        self.opts={"retention_reviewed":True,"max_bytes":1024**2,"timeout_seconds":20}

    def backup(self,name,**kw):
        return lake.backup(name,months=self.months,scopes=self.scopes,
                           **{**self.opts,"quiescent":True,**kw})

    def test_incremental_objects_and_isolated_restore(self):
        a=self.backup("first")
        self.assertEqual(a["file_count"],3)
        self.assertGreater(a["reused_object_bytes"],0)
        b=self.backup("second")
        self.assertEqual(b["new_object_bytes"],0)
        changed=self.required[0]
        changed.write_bytes(b"updated fixture")
        c=self.backup("third")
        self.assertEqual(c["new_object_bytes"],len(b"updated fixture"))
        out=lake.restore_isolated("first","rehearsal",**self.opts)
        root=Path(out["isolated_folder"])
        self.assertEqual((root/changed).read_bytes(),b"partition fixture")
        self.assertEqual(changed.read_bytes(),b"updated fixture")
        self.assertFalse(out["active_data_replaced"])
        self.assertFalse(out["approved_for_model_training"])

    def test_required_partition_missing_and_limits(self):
        with self.assertRaisesRegex(ValueError,"QUIESCENCE"):
            self.backup("denied",quiescent=False)
        with self.assertRaisesRegex(ValueError,"BYTE_BUDGET"):
            self.backup("small",max_bytes=1)
        self.required[0].unlink()
        with self.assertRaises(FileNotFoundError):
            self.backup("missing")
        self.assertFalse(lake._manifest("missing").exists())

    def test_corrupt_object_and_no_overwrite(self):
        self.backup("first")
        with self.assertRaises(FileExistsError):
            self.backup("first")
        m=json.loads(lake._manifest("first").read_text())["manifest"]
        obj=lake._object(m["entries"][0]["sha256"])
        obj.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError,"OBJECT_CORRUPT"):
            lake.verify("first",**self.opts)
        with self.assertRaisesRegex(ValueError,"OBJECT_CORRUPT"):
            self.backup("second")

    def test_manifest_path_tamper_rejected_even_with_recomputed_hash(self):
        self.backup("first")
        p=lake._manifest("first")
        m=json.loads(p.read_text())["manifest"]
        m["entries"][0]["path"]="local_data/other.sqlite3"
        p.write_text(json.dumps({"manifest":m,"sha256":digest(m)}))
        with self.assertRaisesRegex(ValueError,"ENTRY_SCHEMA"):
            lake.restore_isolated("first","bad",**self.opts)
        self.assertFalse((lake.ROOT/"restores"/"bad").exists())

    def test_changed_source_during_copy_not_sealed(self):
        original=lake._copy
        def change(src,dst,expected,end):
            original(src,dst,expected,end)
            self.required[0].write_bytes(b"mutation")
        with patch.object(lake,"_copy",side_effect=change):
            with self.assertRaises(ValueError):
                self.backup("changed")
        self.assertFalse(lake._manifest("changed").exists())

    def test_factor_selection_includes_metadata(self):
        required,optional=lake._plan(self.months,["factors_hf"])
        self.assertEqual(len(required),4)
        self.assertEqual(len(optional),0)
        self.assertEqual({p.suffix for p in required},{".json",".parquet"})
        for p in required:
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(b"factor fixture")
        self.scopes=["factors_hf"]
        out=self.backup("factor")
        self.assertEqual(out["file_count"],4)
        self.assertEqual(lake.verify("factor",**self.opts)["manifest_sha256"],out["manifest_sha256"])
