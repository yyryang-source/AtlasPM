"""Offline CLI smoke test for financing reconciliation."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from atlaspm.cli import main

ROOT = Path(__file__).resolve().parents[1]

class FinancingCLITests(unittest.TestCase):
    def test_fixture_roundtrip(self):
        private = ROOT / "local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as folder:
            capture = Path(folder) / "capture.json"
            output = Path(folder) / "result.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([
                    "ibkr-readonly", "--fixture",
                    str(ROOT / "examples/ibkr_gateway_synthetic.json"),
                    "--account", "DU1234567", "--output", str(capture)
                ]), 0)
            console = io.StringIO()
            with contextlib.redirect_stdout(console):
                self.assertEqual(main([
                    "ibkr-financing-check", "--capture", str(capture),
                    "--mapping", str(ROOT / "examples/ibkr_mapping_synthetic.json"),
                    "--statement", str(ROOT / "examples/ibkr_financing_synthetic.json"),
                    "--output", str(output)
                ]), 0)
            summary = json.loads(console.getvalue())
            full = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "FIELDS_CONSISTENT_REVIEW_REQUIRED")
            self.assertFalse(full["live_execution_enabled"])
            self.assertNotIn("123950", console.getvalue())
            self.assertNotIn("49950", console.getvalue())
            self.assertNotIn("DU1234567", console.getvalue())

if __name__ == "__main__":
    unittest.main()
