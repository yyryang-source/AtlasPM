"""CLI smoke test for offline multi-asset paper order event replay."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main


ROOT = Path(__file__).resolve().parents[1] / "examples"


class PaperOMSCLITests(unittest.TestCase):
    def test_synthetic_replay_reports_all_filled_and_writes_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "oms.json"
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                exit_code = main([
                    "oms-replay",
                    "--ledger", str(ROOT / "institutional_batch_ledger.json"),
                    "--plan", str(ROOT / "institutional_batch_plan.json"),
                    "--events", str(ROOT / "paper_oms_events.json"),
                    "--output", str(output),
                ])
            self.assertEqual(exit_code, 0)
            console = json.loads(capture.getvalue())
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(console["decision"], "PAPER_REPLAY_COMPLETED")
            self.assertFalse(console["live_execution_enabled"])
            self.assertFalse(saved["broker_preview_is_authenticated"])
            self.assertEqual(len(console["orders"]), 4)
            self.assertEqual(len(saved["audit_events"]), 13)
            self.assertNotIn("positions", console["account_replay"])
            self.assertEqual(len(saved["account_replay"]["positions"]), 3)
            self.assertEqual(console["written"], str(output))
            self.assertAlmostEqual(
                console["account_replay"]["marked_net_liquidation"], 123945
            )


if __name__ == "__main__":
    unittest.main()
