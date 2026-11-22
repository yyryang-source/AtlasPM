"""Native CLI model tournament smoke tests and privacy/permissions."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main

ROOT = Path(__file__).resolve().parents[2]


class ModelTournamentCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import lightgbm
        except (ImportError, OSError):
            raise unittest.SkipTest("Install optional pip install -e '.[ml]' for real model fitting")

    def test_offline_training_and_private_champion_report(self):
        root = ROOT / "local_data"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            output = Path(directory) / "selected_models.json"
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = main([
                    "ml-model-tournament",
                    "--file", str(ROOT / "examples" / "synthetic_prices.csv"),
                    "--min-train", "30",
                    "--rebalance-days", "10",
                    "--min-validated-rebalances", "2",
                    "--validation-window", "4",
                    "--mode", "champion",
                    "--output", str(output),
                ])
            self.assertEqual(code, 0)
            console = json.loads(captured.getvalue())
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(console["mode"], "champion")
            self.assertEqual(report["selected_model_family"], "champion")
            self.assertFalse(report["live_execution_enabled"])
            self.assertFalse(report["model_validated_for_live_trading"])
            self.assertEqual(set(report["performance"]),{
                "selected","ridge_only","lightgbm_only",
                "naive_trailing_vol_risk","equal_weight_buy_and_hold",
            })
            self.assertEqual(
                console["written"],
                str(Path("local_data") / output.relative_to(root)),
            )
            self.assertNotIn(str(root.resolve()),captured.getvalue())
            self.assertNotIn("FAKE_A",captured.getvalue())
            self.assertGreater(console["rebalances"],0)

    def test_ensemble_cli_and_reject_overleverage(self):
        root = ROOT / "local_data"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            output = Path(directory) / "ensemble_models.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code=main([
                    "ml-model-tournament", "--mode", "ensemble",
                    "--file", str(ROOT / "examples/synthetic_prices.csv"),
                    "--min-train", "30",
                    "--min-validated-rebalances", "2",
                    "--output", str(output),
                ])
            self.assertEqual(code,0)
            report=json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["selection_policy"]["mode"],"ensemble")
            self.assertTrue(report["selection_policy"]["scoring_uses_only_matured_labels"])
            forbidden=Path(directory)/"leverage.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code=main([
                    "ml-model-tournament",
                    "--file", "examples/synthetic_prices.csv",
                    "--max-exposure", "4",
                    "--output", str(forbidden),
                ])
            self.assertEqual(code,2)
            self.assertFalse(forbidden.exists())

    def test_public_output_is_rejected(self):
        invalid = ROOT / "examples" / "MODEL_DO_NOT_CREATE.json"
        with contextlib.redirect_stderr(io.StringIO()):
            code=main([
                "ml-model-tournament",
                "--file", "examples/synthetic_prices.csv",
                "--output", str(invalid),
            ])
        self.assertEqual(code,2)
        self.assertFalse(invalid.exists())


if __name__=="__main__":
    unittest.main()
