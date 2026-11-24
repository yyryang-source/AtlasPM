"""Fresh-process compatibility checks for relocated Paper workflow modules."""
import subprocess
import sys
import unittest

MODULES=["ibkr_paper_orders","ibkr_whatif","ibkr_next_day","ibkr_equity_snapshot","paper_order_journal","close_drafts","market_close"]
CLI_MODULES=["ibkr_paper_orders","ibkr_whatif","ibkr_next_day","ibkr_equity_snapshot","market_close"]


class ExecutionCompatibilityTests(unittest.TestCase):
    def test_old_import_identity_warning_and_mock(self):
        script = """
import importlib
import warnings
from unittest.mock import patch
for name in ["ibkr_paper_orders","ibkr_whatif","ibkr_next_day","ibkr_equity_snapshot","paper_order_journal","close_drafts","market_close"]:
    target = "atlaspm.execution." + name
    old = "atlaspm." + name
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        module = importlib.import_module(target)
        assert not any(old + " is deprecated" in str(w.message) for w in caught), caught
        legacy = importlib.import_module(old)
        assert legacy is module, name
        assert any(w.category is DeprecationWarning and old + " is deprecated" in str(w.message) for w in caught), name
with patch("atlaspm.ibkr_paper_orders.next_day_review") as mock:
    assert importlib.import_module("atlaspm.execution.ibkr_paper_orders").next_day_review is mock
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_cli_help_legacy_and_canonical(self):
        for name in CLI_MODULES:
            with self.subTest(module=name):
                old = subprocess.run([sys.executable,"-W","always::DeprecationWarning","-m","atlaspm."+name,"--help"],
                                     capture_output=True,text=True,timeout=60)
                new = subprocess.run([sys.executable,"-m","atlaspm.execution."+name,"--help"],
                                     capture_output=True,text=True,timeout=60)
                self.assertEqual(old.returncode,0,old.stderr)
                self.assertEqual(new.returncode,0,new.stderr)
                # Python 3.14 argparse includes the invoked -m module in prog.
                # The public options must match, not the spelling of that path.
                self.assertEqual(old.stdout.replace("atlaspm."+name,"atlaspm.execution."+name).split(),
                                 new.stdout.split())
                self.assertIn("atlaspm."+name+" is deprecated",old.stderr)
