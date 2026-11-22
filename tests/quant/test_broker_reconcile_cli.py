"""CLI smoke tests for read-only imported broker export reconciliation."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main

ROOT = Path(__file__).resolve().parents[2] / "examples"


class BrokerReconcileCLITests(unittest.TestCase):
    def test_exact_synthetic_broker_snapshot_matches_without_live_authority(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "broker_reconcile.json"
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                rc = main([
                    "broker-reconcile",
                    "--ledger", str(ROOT / "institutional_batch_ledger.json"),
                    "--snapshot", str(ROOT / "broker_export_synthetic.json"),
                    "--output", str(out),
                ])
            self.assertEqual(rc, 0)
            console = json.loads(capture.getvalue())
            full = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(console["decision"], "MATCHED_UNVERIFIED_EXPORT")
            self.assertEqual(console["matched_position_count"], 2)
            self.assertEqual(console["snapshot_age_seconds"], 15.0)
            self.assertFalse(console["source_authentication_verified"])
            self.assertFalse(console["live_execution_enabled"])
            self.assertEqual(console["written"], str(out))
            self.assertEqual(full["block_reasons"], [])
            self.assertEqual(full["imported_broker_recognized_treasury_collateral_report_only"], 90000)


if __name__ == "__main__":
    unittest.main()
