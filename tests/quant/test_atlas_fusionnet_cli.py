"""Atlas FusionNet CLI training and safe private research output."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main

ROOT=Path(__file__).resolve().parents[2]


@unittest.skipUnless(importlib.util.find_spec("numpy") is not None,
                     "Optional NumPy missing")
class FusionNetCLITests(unittest.TestCase):
    def test_training_fusion_from_scratch_uses_only_local_data_and_safe_cash(self):
        private=ROOT/"local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as folder:
            target=Path(folder)/"original_fusion_model.json"
            stream=io.StringIO()
            with contextlib.redirect_stdout(stream):
                rc=main([
                    "atlas-native-backtest",
                    "--architecture","fusion",
                    "--return-head-strength","0",
                    "--file",str(ROOT/"examples/synthetic_prices.csv"),
                    "--min-train","60",
                    "--train-window","70",
                    "--epochs","12",
                    "--rebalance-days","10",
                    "--no-trade-band","0.05",
                    "--max-exposure","1",
                    "--output",str(target),
                ])
            self.assertEqual(rc,0)
            report=json.loads(target.read_text(encoding="utf-8"))
            console=json.loads(stream.getvalue())
            self.assertEqual(console["architecture"],"fusion")
            self.assertTrue(console["downside_risk_head_trained"])
            self.assertFalse(console["return_prediction_head_trained"])
            self.assertTrue(console["trained_from_scratch"])
            self.assertFalse(console["live_execution_enabled"])
            self.assertEqual(
                report["final_trained_model_snapshot"]["targets"][1],
                "log_annualized_forward_downside_root_mean_square_return",
            )
            self.assertIsNotNone(
                report["forecast_validation"]["downside_rms_mae_annualized"]
            )
            self.assertNotIn(str(private.resolve()),stream.getvalue())
            self.assertNotIn("FAKE_A",stream.getvalue())

    def test_refuse_leverage_and_invalid_return_model_option(self):
        private=ROOT/"local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as folder:
            path=Path(folder)/"not_created.json"
            for args in (
                ["--return-head-strength","1"],
                ["--return-head-strength","0","--max-exposure","4"],
            ):
                with self.subTest(args=args),contextlib.redirect_stderr(io.StringIO()):
                    rc=main([
                        "atlas-native-backtest",
                        "--architecture","fusion",
                        "--file","examples/synthetic_prices.csv",
                        *args,"--output",str(path),
                    ])
                self.assertEqual(rc,2)
                self.assertFalse(path.exists())


if __name__=="__main__":
    unittest.main()
