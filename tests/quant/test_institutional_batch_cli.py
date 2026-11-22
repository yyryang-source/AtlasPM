"""CLI smoke test for read-only multi-asset basket proposal."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main


EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


class BasketCliTests(unittest.TestCase):
    def test_basket_cli_creates_compact_output_and_full_local_report(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "batch.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                rc = main([
                    "plan-basket",
                    "--ledger", str(EXAMPLES / "institutional_batch_ledger.json"),
                    "--plan", str(EXAMPLES / "institutional_batch_plan.json"),
                    "--output", str(output),
                ])
            self.assertEqual(rc, 0)
            brief = json.loads(stdout.getvalue())
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(brief["decision"], "ALLOW_PAPER_INTENT")
            self.assertFalse(brief["live_execution_enabled"])
            self.assertFalse(saved["broker_preview_is_authenticated"])
            self.assertEqual(len(brief["leg_risk_verdicts"]), 4)
            self.assertAlmostEqual(
                brief["pro_forma_unexecuted"]["net_liquidation"], 123945
            )
            self.assertNotIn("positions", brief["pro_forma_unexecuted"])
            self.assertEqual(len(saved["pro_forma_unexecuted"]["positions"]), 3)
            self.assertEqual(brief["written"], str(output))


if __name__ == "__main__":
    unittest.main()
