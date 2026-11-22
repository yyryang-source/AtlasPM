"""CLI smoke test: read-only institutional multi-asset portfolio ledger."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "institutional_ledger.json"


class InstitutionalLedgerCliTests(unittest.TestCase):
    def test_ledger_command_writes_full_json_and_compact_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "ledger.json"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                code = main([
                    "ledger-report", "--file", str(EXAMPLE), "--output", str(output)
                ])
            self.assertEqual(code, 0)
            summary = json.loads(printed.getvalue())
            detailed = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(summary["written"], str(output))
            self.assertEqual(summary["consolidated"]["net_liquidation"], 162675)
            self.assertEqual(len(summary["accounts"]), 2)
            self.assertNotIn("positions", summary["accounts"][0])
            self.assertIn("positions", detailed["accounts"][0])
            self.assertEqual(detailed["accounts"][1]["positions"][1]["type"], "treasury")
            self.assertFalse(summary["accounts"][1]["account_ledger_ready_for_risk_review"])


if __name__ == "__main__":
    unittest.main()
