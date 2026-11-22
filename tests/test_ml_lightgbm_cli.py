"""Native LightGBM optional CLI smoke and privacy regression."""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main

ROOT=Path(__file__).resolve().parents[1]
HAS_LIGHTGBM=importlib.util.find_spec("lightgbm") is not None


@unittest.skipUnless(HAS_LIGHTGBM,"Optional LightGBM extra not installed")
class NativeLightGBMCLITests(unittest.TestCase):
    def test_offline_train_from_synthetic_prices(self):
        private=ROOT/"local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as folder:
            target=Path(folder)/"open_model_research.json"
            stdout=io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code=main([
                    "ml-rebalance-backtest",
                    "--model","lightgbm",
                    "--file",str(ROOT/"examples/synthetic_prices.csv"),
                    "--min-train","30",
                    "--rebalance-days","10",
                    "--output",str(target),
                ])
            self.assertEqual(code,0)
            report=json.loads(target.read_text(encoding="utf-8"))
            summary=json.loads(stdout.getvalue())
            self.assertEqual(report["selected_model_family"],"lightgbm")
            self.assertIn("lightgbm",summary["model_family"])
            self.assertFalse(summary["live_execution_enabled"])
            self.assertFalse(summary["model_validated_for_live_trading"])
            self.assertEqual(summary["account_hint"] if "account_hint" in summary
                             else None,None)
            self.assertFalse(Path(summary["written"]).is_absolute())
            self.assertNotIn(str(target.resolve()),stdout.getvalue())
            self.assertNotIn("FAKE_A",stdout.getvalue())


if __name__=="__main__":
    unittest.main()
