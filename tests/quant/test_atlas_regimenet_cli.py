"""AtlasPM original native network CLI checks and privacy barriers."""

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
    "Native in-house learning requires pip install -e '.[native]'",
)
class NativeAtlasCLITests(unittest.TestCase):
    def test_actual_scratch_training_and_simulated_rebalances(self):
        private=ROOT/"local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as d:
            report_path=Path(d)/"atlas_private_research.json"
            stdout=io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code=main([
                    "atlas-native-backtest",
                    "--file",str(ROOT/"examples/synthetic_prices.csv"),
                    "--min-train","60",
                    "--train-window","70",
                    "--epochs","12",
                    "--rebalance-days","10",
                    "--output",str(report_path),
                ])
            self.assertEqual(code,0)
            console=json.loads(stdout.getvalue())
            report=json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(console["model_family"],
                             "atlaspm_original_regimenet_v1_dual_head")
            self.assertTrue(console["trained_from_scratch"])
            self.assertTrue(report["final_trained_model_snapshot"]["trained_from_scratch"])
            self.assertGreater(console["automatic_simulated_rebalances"],1)
            self.assertFalse(console["live_execution_enabled"])
            self.assertFalse(report["model_validated_for_live_trading"])
            self.assertFalse(report["IBKR_financing_debt_separately_verified"])
            self.assertEqual(
                console["written"],
                str(Path("local_data")/report_path.relative_to(private)),
            )
            self.assertFalse(Path(console["written"]).is_absolute())
            self.assertNotIn(str(private.resolve()),stdout.getvalue())
            self.assertNotIn("FAKE_A",stdout.getvalue())

    def test_public_file_or_margin_leverage_is_refused(self):
        forbidden=ROOT/"examples"/"DO_NOT_CREATE_native.json"
        with contextlib.redirect_stderr(io.StringIO()):
            code=main([
                "atlas-native-backtest",
                "--file","examples/synthetic_prices.csv",
                "--output",str(forbidden),
            ])
        self.assertEqual(code,2)
        self.assertFalse(forbidden.exists())
        private=ROOT/"local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as d:
            target=Path(d)/"unsafe.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code=main([
                    "atlas-native-backtest",
                    "--file","examples/synthetic_prices.csv",
                    "--min-train","60",
                    "--max-exposure","4",
                    "--output",str(target),
                ])
            self.assertEqual(code,2)
            self.assertFalse(target.exists())


if __name__=="__main__":
    unittest.main()
