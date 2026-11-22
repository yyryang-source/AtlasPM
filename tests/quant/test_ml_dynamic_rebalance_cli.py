"""v0.5.1 cross-platform ML CLI privacy and synthetic backtest tests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main

ROOT = Path(__file__).resolve().parents[1]


class MLDynamicRebalanceCLITests(unittest.TestCase):
    def test_synthetic_ml_training_end_to_end(self):
        private = ROOT / "local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as directory:
            output = Path(directory) / "trained_ml_backtest.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([
                    "ml-rebalance-backtest",
                    "--file", str(ROOT / "examples/synthetic_prices.csv"),
                    "--min-train", "30",
                    "--rebalance-days", "10",
                    "--output", str(output),
                ])
            self.assertEqual(code, 0)
            summary = json.loads(stdout.getvalue())
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "OFFLINE_TRAINED_RESEARCH_ONLY")
            self.assertGreater(summary["rebalances"], 0)
            self.assertGreater(summary["out_of_sample_forecast_count"], 0)
            self.assertEqual(report["model_family"],
                             "supervised_ridge_regression_log_forward_rms_risk")
            self.assertEqual(report["data"]["symbols"], ["FAKE_A", "FAKE_B"])
            self.assertGreater(report["performance"]["ai"]["rebalance_count"], 0)
            self.assertFalse(report["live_execution_enabled"])
            self.assertFalse(report["model_validated_for_live_trading"])
            self.assertNotIn("FAKE_A", stdout.getvalue())
            # JSON escapes Windows backslashes, so never try to strip the
            # serialized absolute path via raw str.replace(str(output), "").
            # A safe report MAY show the output filename, but must NEVER
            # disclose the user's absolute Windows profile path.
            self.assertEqual(
                summary["written"],
                str(Path("local_data") / output.relative_to(private)),
            )
            self.assertFalse(Path(summary["written"]).is_absolute())
            self.assertNotIn(str(private.resolve()), stdout.getvalue())
            self.assertNotIn(str(output.resolve()), stdout.getvalue())
            self.assertNotIn(str(private.resolve()), json.dumps(summary))

    def test_cli_rejects_public_output_and_margin_leverage(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = main([
                "ml-rebalance-backtest",
                "--file", "examples/synthetic_prices.csv",
                "--output", "examples/WRONG.json",
            ])
        self.assertEqual(code, 2)
        self.assertFalse((ROOT / "examples/WRONG.json").exists())
        private = ROOT / "local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as directory:
            dest = Path(directory) / "bad.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = main([
                    "ml-rebalance-backtest",
                    "--file", "examples/synthetic_prices.csv",
                    "--min-train", "30",
                    "--max-exposure", "1.5",
                    "--output", str(dest),
                ])
            self.assertEqual(code, 2)
            self.assertFalse(dest.exists())


if __name__=="__main__":
    unittest.main()
