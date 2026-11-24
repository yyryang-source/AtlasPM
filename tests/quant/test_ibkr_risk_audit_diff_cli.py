"""v0.4.15 audit diff CLI privacy and file protection regression tests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main
from atlaspm.monitoring.ibkr_risk_chain_audit import audit_ibkr_risk_chain
from .test_ibkr_risk_chain_audit import synthetic_chain

ROOT = Path(__file__).resolve().parents[2]


class RiskAuditDiffCLITests(unittest.TestCase):
    def test_cli_compares_two_local_audits_without_printing_sensitive_data(self):
        audit = audit_ibkr_risk_chain(synthetic_chain())
        local = ROOT / "local_data"
        local.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local) as folder:
            previous = Path(folder) / "previous.json"
            current = Path(folder) / "current.json"
            output = Path(folder) / "diff.json"
            previous.write_text(json.dumps(audit), encoding="utf-8")
            current.write_text(json.dumps(audit), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([
                    "ibkr-audit-diff", "--previous", str(previous),
                    "--current", str(current), "--output", str(output),
                ])
            self.assertEqual(code, 0)
            redacted = json.loads(stdout.getvalue())
            private = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(redacted["status"], "NO_SOURCE_CHANGES_DETECTED_UNVERIFIED")
            self.assertEqual(redacted["source_digest_change_count"], 0)
            self.assertFalse(redacted["ready_for_institutional_risk_gate"])
            self.assertEqual(redacted["account_hint"], "****4567")
            self.assertIn("previous_source_sha256", private)
            for forbidden in ("DU1234567", "123950", "49950", "SYN_EQ_A"):
                self.assertNotIn(forbidden, stdout.getvalue())

    def test_cli_rejects_overwriting_audit_and_nonprivate_destination(self):
        local = ROOT / "local_data"
        local.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local) as folder:
            previous = Path(folder) / "previous.json"
            previous.write_text("keep", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                code = main([
                    "ibkr-audit-diff", "--previous", str(previous),
                    "--current", str(previous), "--output", str(previous),
                ])
            self.assertEqual(code, 2)
            self.assertEqual(previous.read_text(encoding="utf-8"), "keep")
            with contextlib.redirect_stderr(io.StringIO()):
                code = main([
                    "ibkr-audit-diff", "--previous", str(previous),
                    "--current", str(previous),
                    "--output", str(ROOT / "examples/SECRET_DIFF.json"),
                ])
            self.assertEqual(code, 2)
            self.assertFalse((ROOT / "examples/SECRET_DIFF.json").exists())


if __name__ == "__main__":
    unittest.main()
