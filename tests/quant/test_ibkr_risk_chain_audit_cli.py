"""CLI test for private v0.4.14 risk-chain audits (zero network)."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main
from test_ibkr_risk_chain_audit import synthetic_chain

ROOT = Path(__file__).resolve().parents[1]


class RiskAuditCLITests(unittest.TestCase):
    def test_five_private_inputs_write_redacted_summary_and_baseline_diff(self):
        data = synthetic_chain()
        local = ROOT / "local_data"
        local.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local) as folder:
            base = Path(folder)
            flags = []
            for name, payload in data.items():
                path = base / f"{name}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                flags.extend(["--" + name, str(path)])
            first = base / "audit_first.json"
            second = base / "audit_second.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "ibkr-risk-audit", *flags, "--output", str(first),
                ])
            self.assertEqual(code, 0)
            summary = json.loads(output.getvalue())
            private = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "HYPOTHETICAL_ALERTS_PRESENT")
            self.assertEqual(summary["integrity_issues"], [])
            self.assertFalse(summary["ready_for_institutional_risk_gate"])
            self.assertTrue(private["source_sha256"]["financing"])
            self.assertNotIn("source_sha256", summary)
            for value in ("DU1234567", "123950", "49950", "SYN_EQ_A"):
                self.assertNotIn(value, output.getvalue())

            with contextlib.redirect_stdout(io.StringIO()):
                code = main([
                    "ibkr-risk-audit", *flags,
                    "--compare", str(first), "--output", str(second),
                ])
            self.assertEqual(code, 0)
            compared = json.loads(second.read_text(encoding="utf-8"))
            self.assertTrue(compared["baseline_comparison_performed"])
            self.assertEqual(compared["integrity_issues"], [])

    def test_refuses_repository_data_leaks_and_input_overwrites(self):
        local = ROOT / "local_data"
        local.mkdir(exist_ok=True)
        private = local / "irreplaceable_audit_input.json"
        with contextlib.redirect_stderr(io.StringIO()):
            code = main([
                "ibkr-risk-audit",
                "--monitor", str(private),
                "--financing", str(private),
                "--stress", str(private),
                "--multiasset", str(private),
                "--reverse", str(private),
                "--output", str(private),
            ])
        self.assertEqual(code, 2)
        self.assertFalse(private.exists())
        with contextlib.redirect_stderr(io.StringIO()):
            code = main([
                "ibkr-risk-audit",
                "--monitor", str(private),
                "--financing", str(private),
                "--stress", str(private),
                "--multiasset", str(private),
                "--reverse", str(private),
                "--output", str(ROOT / "examples" / "SECRET_AUDIT.json"),
            ])
        self.assertEqual(code, 2)
        self.assertFalse((ROOT / "examples" / "SECRET_AUDIT.json").exists())


if __name__ == "__main__":
    unittest.main()
