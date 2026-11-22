"""Offline CLI tests for read-only institutional equity trade proposals."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class EquityPlanCliTests(unittest.TestCase):
    def test_synthetic_buy_writes_proforma_and_paper_rms_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "equity_plan.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                rc = main([
                    "plan-equity", "--ledger", str(EXAMPLES / "institutional_ledger.json"),
                    "--plan", str(EXAMPLES / "institutional_equity_plan.json"),
                    "--output", str(output),
                ])
            self.assertEqual(rc, 0)
            brief = json.loads(stdout.getvalue())
            full = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(brief["decision"], "ALLOW_PAPER_INTENT")
            self.assertFalse(brief["live_execution_enabled"])
            self.assertAlmostEqual(brief["pro_forma_after_unexecuted_trade"]["net_liquidation_at_original_marks"], 45523)
            self.assertEqual(full["rms"]["risk_state"], "NORMAL")
            self.assertIn("broker_preview_verified", full["rms"])
            self.assertEqual(brief["written"], str(output))


if __name__ == "__main__":
    unittest.main()
