"""Institutional paper preflight command CLI smoke test."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main


EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "institutional_scenario.json"


class InstitutionalCliTest(unittest.TestCase):
    def test_cli_reads_synthetic_snapshot_and_never_executes(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "institutional_check.json"
            console = io.StringIO()
            with contextlib.redirect_stdout(console):
                exit_code = main([
                    "institutional-check", "--file", str(EXAMPLE),
                    "--output", str(output),
                ])
            self.assertEqual(exit_code, 0)
            summary = json.loads(console.getvalue())
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(summary["written"], str(output))
            self.assertEqual(saved["decision"], "ALLOW_PAPER_INTENT")
            self.assertFalse(saved["live_execution_enabled"])
            self.assertEqual(saved["risk_state"], "NORMAL")


if __name__ == "__main__":
    unittest.main()
