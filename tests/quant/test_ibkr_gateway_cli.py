"""CLI smoke tests for zero-network IBKR Gateway capture and privacy guard."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main


ROOT = Path(__file__).resolve().parents[1]


class IBKRGatewayCLITests(unittest.TestCase):
    def test_fixture_capture_prints_redacted_summary(self):
        private = ROOT / "local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as folder:
            output = Path(folder) / "synthetic_ibkr.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                rc = main([
                    "ibkr-readonly", "--fixture",
                    str(ROOT / "examples" / "ibkr_gateway_synthetic.json"),
                    "--account", "DU1234567",
                    "--output", str(output),
                ])
            self.assertEqual(rc, 0)
            console = json.loads(stdout.getvalue())
            raw = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(console["account_hint"], "****4567")
            self.assertEqual(console["position_count"], 2)
            self.assertFalse(console["gateway_tls_certificate_validated"])
            self.assertFalse(console["ready_for_institutional_risk_gate"])
            self.assertNotIn("DU1234567", stdout.getvalue())
            self.assertNotIn("123950", stdout.getvalue())
            self.assertFalse(raw["live_execution_enabled"])
            self.assertEqual(raw["raw"]["selected_account"]["accountId"], "DU1234567")
            self.assertEqual(console["written"], str(output))

    def test_command_requires_explicit_gateway_optin_or_fixture(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = main(["ibkr-readonly", "--account", "DU1234567"])
        self.assertEqual(rc, 2)

    def test_raw_snapshot_cannot_be_written_to_public_examples(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = main([
                "ibkr-readonly",
                "--fixture", str(ROOT / "examples" / "ibkr_gateway_synthetic.json"),
                "--account", "DU1234567",
                "--output", str(ROOT / "examples" / "LEAK.json"),
            ])
        self.assertEqual(rc, 2)
        self.assertFalse((ROOT / "examples" / "LEAK.json").exists())


if __name__ == "__main__":
    unittest.main()
