"""Independent benchmark alignment and CLI validation (no network)."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.benchmark import compare_spy
from atlaspm.cli import main


def _case(leverage: float) -> dict:
    curve = [
        {"date": "2024-01-03", "equity_index": 1.02,
         "benchmark_equity_index": 1.03, "drawdown": 0.0,
         "below_assumed_maintenance": False},
        {"date": "2024-01-04", "equity_index": 0.99,
         "benchmark_equity_index": 1.05, "drawdown": -0.0294117647,
         "below_assumed_maintenance": False},
        {"date": "2024-01-05", "equity_index": 1.04,
         "benchmark_equity_index": 1.08, "drawdown": 0.0,
         "below_assumed_maintenance": False},
    ]
    return {
        "data": {
            "symbols": ["A", "B"],
            "first_backtest_return_date": "2024-01-03",
            "last_backtest_return_date": "2024-01-05",
            "backtest_daily_observations": 3,
        },
        "parameters": {
            "target_leverage": leverage, "risk_aversion": 4.0,
            "lookback_trading_days": 60, "rebalance_every_trading_days": 10,
            "max_weight": 0.6, "trading_cost_basis_points_each_side": 5.0,
        },
        "strategy": {
            "annualized_compounded_return": 0.08,
            "annualized_volatility": 0.25,
            "max_drawdown": -0.03, "sharpe_zero_risk_free_assumption": 0.6,
            "ending_equity_index": 1.04, "below_assumed_maintenance_days": 0,
        },
        "benchmark": {
            "type": "equal_weight_buy_and_hold_same_symbols_no_costs",
            "annualized_compounded_return": 0.12, "max_drawdown": -0.5,
        },
        "rebalances": [{"execution_date": "2024-01-02"}],
        "equity_curve": curve,
    }


class SpyBenchmarkTest(unittest.TestCase):
    def _fixtures(self, folder: str, missing: bool = False):
        cash = Path(folder) / "cash.json"
        margin = Path(folder) / "margin.json"
        spy = Path(folder) / "spy.csv"
        cash.write_text(json.dumps(_case(1)), encoding="utf-8")
        margin.write_text(json.dumps(_case(2)), encoding="utf-8")
        with spy.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "symbol", "adjusted_close"])
            writer.writerows([
                ("2024-01-02", "SPY", 100),
                ("2024-01-03", "SPY", 110),
                *([] if missing else [("2024-01-04", "SPY", 108)]),
                ("2024-01-05", "SPY", 121),
            ])
        return cash, margin, spy

    def test_independent_spy_return_and_date_grid(self):
        with tempfile.TemporaryDirectory() as folder:
            cash, margin, spy = self._fixtures(folder)
            report = compare_spy(cash, margin, spy)
        self.assertEqual(report["backtest_observations"], 3)
        self.assertAlmostEqual(report["spy"]["ending_equity_index"], 1.21)
        self.assertEqual(report["spy_curve"][0]["date"], "2024-01-03")
        self.assertEqual(report["strategy_universe"], ["A", "B"])
        self.assertNotIn("SPY", report["strategy_universe"])
        self.assertIn("active_risk", report)
        self.assertEqual(len(report["active_risk"]["calendar_years"]), 1)
        self.assertIn("beta_to_spy", report["active_risk"]["full_period"]["cash"])

    def test_missing_date_is_rejected_not_forward_filled(self):
        with tempfile.TemporaryDirectory() as folder:
            cash, margin, spy = self._fixtures(folder, missing=True)
            with self.assertRaisesRegex(ValueError, "missing"):
                compare_spy(cash, margin, spy)

    def test_cli_command_writes_complete_curve(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as folder:
            cash, margin, spy = self._fixtures(folder)
            destination = Path(folder) / "spy_comp.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([
                    "benchmark-spy", "--cash", str(cash), "--margin", str(margin),
                    "--spy", str(spy), "--output", str(destination)
                ])
            self.assertEqual(code, 0)
            console = json.loads(stdout.getvalue())
            self.assertNotIn("spy_curve", console)
            self.assertNotIn("calendar_years", console["active_risk"])
            self.assertTrue(console["active_risk"]["annual_breakdown_saved_to_file"])
            saved = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["spy_curve"]), 3)
            self.assertEqual(len(saved["active_risk"]["calendar_years"]), 1)


if __name__ == "__main__":
    unittest.main()
