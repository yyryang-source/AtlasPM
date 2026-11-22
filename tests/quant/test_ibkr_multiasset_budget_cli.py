"""Synthetic CLI smoke tests for private multi-asset risk budget workflow."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main
from atlaspm.ibkr_financing import reconcile_ibkr_financing_margin
from atlaspm.ibkr_gateway import FixtureGatewayTransport, collect_ibkr_readonly_account
from atlaspm.ibkr_margin_monitor import collect_ibkr_margin_monitor
from atlaspm.ibkr_margin_stress import run_margin_stress

ROOT = Path(__file__).resolve().parents[1]


class MultiassetBudgetCLITests(unittest.TestCase):
    def test_synthetic_pipeline_console_is_redacted(self):
        fixture = json.loads(
            (ROOT / "examples/ibkr_gateway_synthetic.json").read_text(encoding="utf-8")
        )
        capture = collect_ibkr_readonly_account(
            FixtureGatewayTransport(fixture), account_id="DU1234567", fixture_mode=True
        )
        monitor = collect_ibkr_margin_monitor(
            FixtureGatewayTransport(fixture), account_id="DU1234567", fixture_mode=True
        )
        mapping = json.loads(
            (ROOT / "examples/ibkr_mapping_synthetic.json").read_text(encoding="utf-8")
        )
        statement = json.loads(
            (ROOT / "examples/ibkr_financing_synthetic.json").read_text(encoding="utf-8")
        )
        financing = reconcile_ibkr_financing_margin(capture, mapping, statement)
        scenarios = json.loads(
            (ROOT / "examples/ibkr_margin_stress_synthetic.json").read_text(encoding="utf-8")
        )
        old_report = run_margin_stress(monitor, financing, scenarios)
        local = ROOT / "local_data"
        local.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local) as temp:
            previous = Path(temp) / "old_stress.json"
            destination = Path(temp) / "multiasset.json"
            previous.write_text(json.dumps(old_report), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main([
                    "ibkr-multiasset-budget",
                    "--stress", str(previous),
                    "--assumptions", str(
                        ROOT / "examples/ibkr_multiasset_budget_synthetic.json"
                    ),
                    "--output", str(destination),
                ])
            self.assertEqual(code, 0)
            displayed = json.loads(out.getvalue())
            private = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(displayed["status"], "HYPOTHETICAL_RISK_BUDGET_ONLY")
            self.assertEqual(displayed["asset_sleeve_count"], 3)
            self.assertEqual(len(displayed["scenarios"]), 3)
            self.assertFalse(private["ready_for_institutional_risk_gate"])
            for secret in ("DU1234567", "123950", "49950", "SYN_EQ_A"):
                self.assertNotIn(secret, out.getvalue())

    def test_output_and_prior_report_must_be_private(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = main([
                "ibkr-multiasset-budget", "--stress",
                str(ROOT / "examples/ibkr_margin_stress_synthetic.json"),
                "--assumptions", str(ROOT / "examples/ibkr_multiasset_budget_synthetic.json"),
                "--output", str(ROOT / "examples/PRIVATE_BUDGET.json"),
            ])
        self.assertEqual(rc, 2)
        self.assertFalse((ROOT / "examples/PRIVATE_BUDGET.json").exists())


if __name__ == "__main__":
    unittest.main()
