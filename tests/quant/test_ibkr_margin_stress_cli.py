"""PowerShell CLI privacy smoke tests for v0.4.11 synthetic offline stress."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main
from atlaspm.risk.ibkr_financing import reconcile_ibkr_financing_margin
from atlaspm.execution.ibkr_gateway import FixtureGatewayTransport, collect_ibkr_readonly_account
from atlaspm.risk.ibkr_margin_monitor import collect_ibkr_margin_monitor

ROOT = Path(__file__).resolve().parents[2]


class StressCLITests(unittest.TestCase):
    def test_end_to_end_and_private_export(self):
        fixture = json.loads(
            (ROOT / "examples/ibkr_gateway_synthetic.json").read_text(encoding="utf-8")
        )
        mapping = json.loads(
            (ROOT / "examples/ibkr_mapping_synthetic.json").read_text(encoding="utf-8")
        )
        statement = json.loads(
            (ROOT / "examples/ibkr_financing_synthetic.json").read_text(encoding="utf-8")
        )
        capture = collect_ibkr_readonly_account(
            FixtureGatewayTransport(fixture), account_id="DU1234567", fixture_mode=True
        )
        monitor = collect_ibkr_margin_monitor(
            FixtureGatewayTransport(fixture), account_id="DU1234567", fixture_mode=True
        )
        finance = reconcile_ibkr_financing_margin(capture, mapping, statement)
        local = ROOT / "local_data"
        local.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local) as folder:
            m = Path(folder) / "monitor.json"
            f = Path(folder) / "financing.json"
            dest = Path(folder) / "stress.json"
            m.write_text(json.dumps(monitor), encoding="utf-8")
            f.write_text(json.dumps(finance), encoding="utf-8")
            console = io.StringIO()
            with contextlib.redirect_stdout(console):
                rc = main([
                    "ibkr-stress", "--monitor", str(m),
                    "--financing", str(f),
                    "--scenarios", str(ROOT / "examples/ibkr_margin_stress_synthetic.json"),
                    "--output", str(dest),
                ])
            self.assertEqual(rc, 0)
            display = json.loads(console.getvalue())
            private = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(display["status"], "HYPOTHETICAL_STRESS_ONLY")
            self.assertEqual(len(private["scenarios"]), 4)
            self.assertFalse(private["live_execution_enabled"])
            for leak in ("DU1234567", "49950", "123950", "60000"):
                self.assertNotIn(leak, console.getvalue())

    def test_blocks_output_outside_ignored_folder(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = main([
                "ibkr-stress", "--monitor", "invalid.json",
                "--financing", "invalid.json",
                "--scenarios", "invalid.json",
                "--output", str(ROOT / "examples" / "private-stress.json"),
            ])
        self.assertEqual(code, 2)
        self.assertFalse((ROOT / "examples" / "private-stress.json").exists())


if __name__ == "__main__":
    unittest.main()
