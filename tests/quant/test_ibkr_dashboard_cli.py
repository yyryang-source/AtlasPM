"""PowerShell-like one-command local HTML dashboard CLI regression tests."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main
from atlaspm.monitoring.ibkr_risk_chain_audit import audit_ibkr_risk_chain
from atlaspm.monitoring.ibkr_risk_audit_diff import compare_ibkr_risk_audits
from .test_ibkr_risk_chain_audit import synthetic_chain


def write_input_reports(folder: Path):
    reports = synthetic_chain()
    prior = audit_ibkr_risk_chain(reports)
    current = audit_ibkr_risk_chain(reports, baseline_audit=prior)
    diff = compare_ibkr_risk_audits(prior, current)
    names = {
        "monitor": "margin_monitor_fixture.json",
        "financing": "ibkr_financing_check.json",
        "stress": "ibkr_margin_stress.json",
        "multiasset": "ibkr_multiasset_budget.json",
        "reverse": "ibkr_reverse_stress.json",
    }
    for name, filename in names.items():
        (folder / filename).write_text(json.dumps(reports[name]), encoding="utf-8")
    (folder / "ibkr_risk_audit.json").write_text(json.dumps(prior), encoding="utf-8")
    (folder / "ibkr_risk_audit_second.json").write_text(json.dumps(current), encoding="utf-8")
    (folder / "ibkr_risk_audit_diff.json").write_text(json.dumps(diff), encoding="utf-8")


class DashboardCLITests(unittest.TestCase):
    def with_temp_project(self):
        return tempfile.TemporaryDirectory()

    def test_cli_single_command_renders_offline_dashboard(self):
        with self.with_temp_project() as tmp:
            workspace = Path(tmp)
            local = workspace / "local_data"
            local.mkdir()
            write_input_reports(local)
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    result = main(["ibkr-dashboard"])
                self.assertEqual(result, 0)
                summary = json.loads(stdout.getvalue())
                html_path = local / "atlaspm_dashboard.html"
                self.assertTrue(html_path.is_file())
                html = html_path.read_text(encoding="utf-8")
                self.assertEqual(summary["dashboard_state"], "LOCAL_SIMULATED_ALERTS_PRESENT")
                self.assertEqual(summary["hypothetical_alert_count_total"], 5)
                self.assertFalse(summary["live_execution_enabled"])
                self.assertTrue(html.startswith("<!doctype html>"))
                for leak in ("DU1234567", "49950", "123950", "SYN_EQ_A"):
                    self.assertNotIn(leak, html)
                    self.assertNotIn(leak, stdout.getvalue())
            finally:
                os.chdir(cwd)

    def test_refuse_external_inputs_and_output(self):
        with self.with_temp_project() as tmp:
            workspace = Path(tmp)
            local = workspace / "local_data"
            local.mkdir()
            write_input_reports(local)
            outside = workspace / "outside"
            outside.mkdir()
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main([
                        "ibkr-dashboard", "--folder", str(outside),
                    ]), 2)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main([
                        "ibkr-dashboard", "--output", "public.html",
                    ]), 2)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main([
                        "ibkr-dashboard", "--output", "local_data/accidental.json",
                    ]), 2)
                self.assertFalse((workspace / "public.html").exists())
            finally:
                os.chdir(cwd)

    def test_missing_private_audit_diff_fails_without_writing_html(self):
        with self.with_temp_project() as tmp:
            workspace = Path(tmp)
            local = workspace / "local_data"
            local.mkdir()
            write_input_reports(local)
            (local / "ibkr_risk_audit_diff.json").unlink()
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["ibkr-dashboard"]), 2)
                self.assertFalse((local / "atlaspm_dashboard.html").exists())
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
