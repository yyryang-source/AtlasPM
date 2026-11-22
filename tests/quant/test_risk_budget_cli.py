"""Offline command-line tests for research-only risk-budget checks."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main


class RiskBudgetCliTests(unittest.TestCase):
    def test_cli_outputs_risk_report(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "estimates.json"
            output = Path(folder) / "risk.json"
            source.write_text(json.dumps({
                "expected_returns": {"A": 0.10, "B": 0.09},
                "covariance": [[0.04, 0.0], [0.0, 0.04]],
                "metadata": {"return_observations": 2000},
            }), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main([
                    "risk-check", "--file", str(source),
                    "--leverage", "1", "--max-weight", "0.6",
                    "--output", str(output)
                ])
            self.assertEqual(result, 0)
            console = json.loads(stdout.getvalue())
            self.assertTrue(output.exists())
            self.assertEqual(console["input_metadata"]["return_observations"], 2000)
            self.assertEqual(console["written"], str(output))
            self.assertEqual(console["status"], "WITHIN_RESEARCH_BUDGET")
            self.assertFalse(console["candidate_optimizer"]["is_trained_ai"])


if __name__ == "__main__":
    unittest.main()
