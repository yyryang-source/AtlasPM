"""Actual independent Atlas RiskNet v2 CLI smoke and privacy checks."""

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
    "Native risk model requires optional numpy",
)
class RiskNetCLITests(unittest.TestCase):
    def test_train_real_single_risk_head_without_pretrained_models(self):
        root=ROOT/"local_data"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            out=Path(directory)/"risknet_research.json"
            stream=io.StringIO()
            with contextlib.redirect_stdout(stream):
                code=main([
                    "atlas-native-backtest",
                    "--architecture","risknet",
                    "--return-head-strength","0",
                    "--file",str(ROOT/"examples/synthetic_prices.csv"),
                    "--min-train","60",
                    "--train-window","70",
                    "--epochs","12",
                    "--no-trade-band",".05",
                    "--rebalance-days","10",
                    "--output",str(out),
                ])
            self.assertEqual(code,0)
            console=json.loads(stream.getvalue())
            result=json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(result["architecture_choice"],"risknet")
            self.assertFalse(result["return_prediction_head_trained"])
            self.assertIsNone(result["forecast_validation"]["return_mae_annualized"])
            self.assertEqual(console["architecture"],"risknet")
            self.assertFalse(console["return_prediction_head_trained"])
            self.assertTrue(console["trained_from_scratch"])
            self.assertFalse(console["live_execution_enabled"])
            self.assertGreater(console["automatic_simulated_rebalances"],0)
            self.assertEqual(console["no_trade_band_l1_account_weight"],.05)
            self.assertFalse(Path(console["written"]).is_absolute())
            self.assertNotIn(str(ROOT.resolve()),stream.getvalue())
            self.assertNotIn("FAKE_A",stream.getvalue())

    def test_nonzero_return_head_or_live_leverage_is_rejected(self):
        private=ROOT/"local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as directory:
            for args in (
                ["--return-head-strength","1"],
                ["--max-exposure","4","--return-head-strength","0"],
            ):
                path=Path(directory)/"must_not_exist.json"
                with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                    result=main([
                        "atlas-native-backtest","--architecture","risknet",
                        "--file","examples/synthetic_prices.csv",
                        *args,"--output",str(path),
                    ])
                self.assertEqual(result,2)
                self.assertFalse(path.exists())


if __name__=="__main__":
    unittest.main()
