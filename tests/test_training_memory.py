import os
import tempfile
import unittest
from pathlib import Path
from importlib.util import find_spec
from atlaspm.research.training_memory import FoldArrays
from atlaspm.research.training_dataset import build_panel, sequence_for, purged_folds
from test_training_engine import fixture


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
