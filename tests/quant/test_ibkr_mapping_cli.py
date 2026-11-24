"""Zero-network CLI tests for private IBKR security-identity mapping."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main
from atlaspm.execution.ibkr_gateway import FixtureGatewayTransport, collect_ibkr_readonly_account


ROOT = Path(__file__).resolve().parents[2]


class IBKRMapCLITests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads(
            (ROOT / "examples" / "ibkr_gateway_synthetic.json").read_text(
                encoding="utf-8"
            )
        )
        self.capture = collect_ibkr_readonly_account(
            FixtureGatewayTransport(fixture), account_id="DU1234567",
            fixture_mode=True,
        )

    def test_private_capture_mapping_reports_only_redacted_diagnostics(self):
        local = ROOT / "local_data"
        local.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local) as tmp:
            capture_path = Path(tmp) / "captured.json"
            report_path = Path(tmp) / "mapped.json"
            capture_path.write_text(
                json.dumps(self.capture), encoding="utf-8"
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([
                    "ibkr-map-check",
                    "--capture", str(capture_path),
                    "--mapping", str(
                        ROOT / "examples" / "ibkr_mapping_synthetic.json"
                    ),
                    "--output", str(report_path),
                ])
            self.assertEqual(code, 0)
            summary = json.loads(stdout.getvalue())
            full = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["mapping_status"], "IDENTITIES_MAPPED_REVIEW_REQUIRED")
            self.assertEqual(summary["mapped_contract_count"], 2)
            self.assertEqual(summary["account_hint"], "****4567")
            self.assertFalse(summary["ready_for_institutional_risk_gate"])
            self.assertFalse(full["source_authentication_independently_proven"])
            self.assertEqual(full["positions"][1]["atlaspm_type"], "treasury")
            for private in ("DU1234567", "123950", "101010", "202020"):
                self.assertNotIn(private, stdout.getvalue())

    def test_private_report_cannot_write_to_public_examples_folder(self):
        local = ROOT / "local_data"
        local.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local) as tmp:
            capture_path = Path(tmp) / "captured.json"
            capture_path.write_text(
                json.dumps(self.capture), encoding="utf-8"
            )
            bad = ROOT / "examples" / "BAD-PRIVATE-MAPPING.json"
            with contextlib.redirect_stderr(io.StringIO()):
                rc = main([
                    "ibkr-map-check",
                    "--capture", str(capture_path),
                    "--mapping", str(
                        ROOT / "examples" / "ibkr_mapping_synthetic.json"
                    ),
                    "--output", str(bad),
                ])
            self.assertEqual(rc, 2)
            self.assertFalse(bad.exists())


if __name__ == "__main__":
    unittest.main()
