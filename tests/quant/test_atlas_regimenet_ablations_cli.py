"""Native Atlas 0.6.1 new CLI ablation command regression tests."""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main

ROOT=Path(__file__).resolve().parents[2]


@unittest.skipUnless(
    importlib.util.find_spec("numpy") is not None,
    "Optional NumPy model backend not installed",
)
class AtlasAblationCLITests(unittest.TestCase):
    def test_return_risk_ablation_with_safe_band_and_private_report(self):
        root=ROOT/"local_data"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder:
            output=Path(folder)/"ablation.json"
            stream=io.StringIO()
            with contextlib.redirect_stdout(stream):
                code=main([
                    "atlas-native-backtest",
                    "--file",str(ROOT/"examples/synthetic_prices.csv"),
                    "--min-train","60",
                    "--train-window","70",
                    "--epochs","12",
                    "--rebalance-days","10",
                    "--return-head-strength","0",
                    "--no-trade-band","0.10",
                    "--output",str(output),
                ])
            self.assertEqual(code,0)
            report=json.loads(output.read_text(encoding="utf-8"))
            summary=json.loads(stream.getvalue())
            self.assertEqual(report["policy"]["return_head_strength"],0)
            self.assertEqual(report["policy"]["no_trade_band_l1_account_weight"],.1)
            self.assertIn("naive_trailing_risk_mae_annualized",report["forecast_validation"])
            self.assertIn("zero_return_baseline_mae_annualized",report["forecast_validation"])
            self.assertEqual(summary["return_head_strength"],0)
            self.assertEqual(summary["no_trade_band_l1_account_weight"],.1)
            self.assertFalse(report["live_execution_enabled"])
            self.assertNotIn(str(ROOT.resolve()),stream.getvalue())
            self.assertNotIn("FAKE_A",stream.getvalue())

    def test_unsafe_model_tuning_rejected_before_file_write(self):
        private=ROOT/"local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as d:
            for arg,val in (
                ("--return-head-strength","1.1"),
                ("--return-head-strength","-0.01"),
                ("--no-trade-band","0.6"),
                ("--max-exposure","4"),
            ):
                target=Path(d)/"should_not_exist.json"
                with self.subTest(arg=arg,val=val):
                    with contextlib.redirect_stderr(io.StringIO()):
                        status=main([
                            "atlas-native-backtest",
                            "--file","examples/synthetic_prices.csv",
                            arg,val,
                            "--output",str(target),
                        ])
                    self.assertEqual(status,2)
                    self.assertFalse(target.exists())


if __name__=="__main__":
    unittest.main()
