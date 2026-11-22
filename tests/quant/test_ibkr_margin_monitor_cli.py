"""IBKR margin monitor PowerShell-friendly CLI smoke tests (no network)."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main

ROOT = Path(__file__).resolve().parents[1]


class IBKRMarginMonitorCLITests(unittest.TestCase):
    def test_redacted_synthetic_cli_and_private_output(self):
        local = ROOT / "local_data"
        local.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local) as tmp:
            dest = Path(tmp) / "margin.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main([
                    "ibkr-margin-monitor", "--account", "DU1234567",
                    "--fixture", str(ROOT / "examples/ibkr_gateway_synthetic.json"),
                    "--output", str(dest),
                ])
            self.assertEqual(result, 0)
            redacted = json.loads(output.getvalue())
            full = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(redacted["monitor_state"], "NORMAL")
            self.assertFalse(redacted["live_execution_enabled"])
            self.assertEqual(redacted["account_hint"], "****4567")
            self.assertIn("raw_portfolio_summary", full)
            for private in ("DU1234567", "123950", "80000", "110000"):
                self.assertNotIn(private, output.getvalue())

    def test_requires_explicit_fixture_or_gateway(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["ibkr-margin-monitor", "--account", "DU1234567"]), 2)

    def test_cannot_export_to_repository_examples(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([
                "ibkr-margin-monitor", "--account", "DU1234567",
                "--fixture", str(ROOT / "examples/ibkr_gateway_synthetic.json"),
                "--output", str(ROOT / "examples" / "PRIVATE_MARGIN.json"),
            ]), 2)
        self.assertFalse((ROOT / "examples" / "PRIVATE_MARGIN.json").exists())


if __name__ == "__main__":
    unittest.main()
