import os
import tempfile
import unittest
from pathlib import Path
from importlib.util import find_spec
from atlaspm.research.training_memory import FoldArrays
from atlaspm.research.training_dataset import build_panel, sequence_for, purged_folds
from .test_training_engine import fixture


class CompactTests(unittest.TestCase):
    def test_compact_equivalence(self):
        p=fixture()
        del p["sessions"][22]["assets"][0]
        a=build_panel(p,5,3)
        b=build_panel(p,5,3,compact=True)
        self.assertEqual(a["missing_label_paths"],b["missing_label_paths"])
        for x,y in zip(a["prediction_samples"],b["prediction_samples"]):
            self.assertEqual(x,{**y,"sequence":sequence_for(b,y)})
        self.assertEqual(purged_folds(a,minimum_train_sessions=10,test_sessions=5),
                         purged_folds(b,minimum_train_sessions=10,test_sessions=5))


@unittest.skipUnless(find_spec("numpy"),"numpy required")
class MemoryTests(unittest.TestCase):
    def test_budget_refuses_unconfigured_spill(self):
        with FoldArrays(1) as arrays:
            with self.assertRaisesRegex(ValueError,"ARRAY_BUDGET_EXCEEDED"):
                arrays.allocate((200000,))

    def test_spill_cleanup_and_values(self):
        import numpy as np
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with FoldArrays(1,"local_data/cache") as arrays:
                    a=arrays.allocate((200000,))
                    self.assertIsInstance(a,np.memmap)
                    a[:]=2.
                    self.assertEqual(float(a.sum()),400000.)
                    self.assertEqual(arrays.spilled,1600000)
                self.assertEqual(list(Path("local_data/cache").iterdir()),[])
            finally:
                os.chdir(cwd)

    def test_cleanup_on_exception(self):
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with self.assertRaisesRegex(RuntimeError,"test"):
                    with FoldArrays(1,"local_data/cache") as arrays:
                        arrays.allocate((200000,))
                        raise RuntimeError("test")
                self.assertEqual(list(Path("local_data/cache").iterdir()),[])
            finally:
                os.chdir(cwd)


    def test_disk_panel_and_training_equivalence(self):
        from atlaspm.research.training_compact import feature_workspace
        from atlaspm.research.training_engine import train
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                p=fixture()
                del p["sessions"][22]["assets"][0]
                reference=build_panel(p,5,3,compact=True)
                with feature_workspace("local_data/features") as column:
                    disk=build_panel(p,5,3,compact=True,feature_column=column)
                    self.assertEqual(list(reference["prediction_samples"]),list(disk["prediction_samples"]))
                    self.assertEqual(purged_folds(reference,minimum_train_sessions=10,test_sessions=5),
                                     purged_folds(disk,minimum_train_sessions=10,test_sessions=5))
                    self.assertEqual(reference["prediction_samples"].numeric_bytes,
                                     disk["prediction_samples"].numeric_bytes)
                self.assertEqual(list(Path("local_data/features").iterdir()),[])
                a=train(p,minimum_train_sessions=10)
                b=train(p,minimum_train_sessions=10,feature_cache_dir="local_data/features")
                self.assertEqual(a["predictions"],b["predictions"])
                self.assertEqual(a["metrics"],b["metrics"])
                self.assertEqual(b["feature_storage"],"memmap")
                self.assertEqual(list(Path("local_data/features").iterdir()),[])
            finally:
                os.chdir(cwd)

    def test_disk_panel_cleanup_after_invalid_input(self):
        from atlaspm.research.training_engine import train
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                p=fixture()
                p["mode"]="historical"
                with self.assertRaisesRegex(ValueError,"REAL_TRAINING_DATA_NOT_APPROVED"):
                    train(p,feature_cache_dir="local_data/features")
                # Admission precedes allocation: rejected data must not create
                # even the cache root, much less a temporary feature workspace.
                self.assertFalse(Path("local_data").exists())
            finally:
                os.chdir(cwd)


    def test_disk_panel_cleanup_after_late_validation_failure(self):
        from atlaspm.research.training_engine import train
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                p=fixture()
                # Valid metadata opens the workspace; a late future feature
                # fails after earlier sessions have populated the disk cache.
                p["sessions"][-1]["assets"][0]["available_at"]="2021-01-01T16:00:00-05:00"
                cache=Path("local_data/features")
                cache.mkdir(parents=True)
                sentinel=cache/"existing.txt"
                sentinel.write_text("preserve",encoding="utf-8")
                with self.assertRaisesRegex(ValueError,"FEATURE_LOOKAHEAD"):
                    train(p,feature_cache_dir=str(cache))
                self.assertEqual(list(cache.iterdir()),[sentinel])
                self.assertEqual(sentinel.read_text(encoding="utf-8"),"preserve")
            finally:
                os.chdir(cwd)

    def test_fold_index_prefix_is_shared(self):
        from atlaspm.research.training_compact import IndexPrefix
        from atlaspm.research.training_dataset import iter_purged_folds
        p=build_panel(fixture(),5,compact=True)
        folds=list(iter_purged_folds(p,minimum_train_sessions=10,test_sessions=5))
        for fold in folds:
            self.assertIsInstance(fold["train"],IndexPrefix)
            self.assertIs(fold["train"].indices,p["samples"].indices)
        self.assertEqual([list(f["train"]) for f in folds],
                         [f["train"] for f in purged_folds(build_panel(fixture(),5),
                                                          minimum_train_sessions=10,test_sessions=5)])
