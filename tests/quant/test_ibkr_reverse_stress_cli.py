"""CLI tests: v0.4.13 reverse stress must not print private scenario data."""

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
from atlaspm.risk.ibkr_margin_stress import run_margin_stress
from atlaspm.risk.ibkr_multiasset_budget import analyze_multiasset_risk_budget

ROOT = Path(__file__).resolve().parents[2]


class IBKRReverseCLITests(unittest.TestCase):
    def load(self, filename):
        return json.loads((ROOT / "examples" / filename).read_text(encoding="utf-8"))

    def test_full_offline_reverse_stress_cli(self):
        fixture = self.load("ibkr_gateway_synthetic.json")
        capture = collect_ibkr_readonly_account(
            FixtureGatewayTransport(fixture), account_id="DU1234567",
            fixture_mode=True,
        )
        monitor = collect_ibkr_margin_monitor(
            FixtureGatewayTransport(fixture), account_id="DU1234567",
            fixture_mode=True,
        )
        finance = reconcile_ibkr_financing_margin(
            capture, self.load("ibkr_mapping_synthetic.json"),
            self.load("ibkr_financing_synthetic.json"),
        )
        stress = run_margin_stress(
            monitor, finance, self.load("ibkr_margin_stress_synthetic.json"),
        )
        budget = analyze_multiasset_risk_budget(
            stress, self.load("ibkr_multiasset_budget_synthetic.json"),
        )
        local = ROOT / "local_data"
        local.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local) as temp:
            prior = Path(temp) / "budget.json"
            saved = Path(temp) / "reverse.json"
            prior.write_text(json.dumps(budget), encoding="utf-8")
            console = io.StringIO()
            with contextlib.redirect_stdout(console):
                code = main([
                    "ibkr-reverse-stress",
                    "--budget", str(prior),
                    "--assumptions", str(ROOT / "examples/ibkr_reverse_stress_synthetic.json"),
                    "--output", str(saved),
                ])
            self.assertEqual(code, 0)
            summary = json.loads(console.getvalue())
            private = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "HYPOTHETICAL_REVERSE_STRESS_ONLY")
            self.assertEqual(len(summary["cases"]), 4)
            self.assertEqual(summary["account_hint"], "****4567")
            self.assertFalse(private["live_execution_enabled"])
            for sensitive in ("DU1234567", "123950", "49950", "SYN_EQ_A"):
                self.assertNotIn(sensitive, console.getvalue())

    def test_prior_and_destination_must_be_private(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = main([
                "ibkr-reverse-stress",
                "--budget", "examples/ibkr_multiasset_budget_synthetic.json",
                "--assumptions", "examples/ibkr_reverse_stress_synthetic.json",
                "--output", "examples/LEAKED_REVERSE.json",
            ])
        self.assertEqual(code, 2)
        self.assertFalse((ROOT / "examples/LEAKED_REVERSE.json").exists())


if __name__ == "__main__":
    unittest.main()
