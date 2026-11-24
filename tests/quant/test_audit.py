"""Risk audit checks for matching cash/margin JSON backtests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.monitoring.audit import audit_backtests
from atlaspm.cli import main


def example_report(leverage: float, breaches: bool = False) -> dict:
    curve = [
        {"date": "2023-12-29", "equity_index": 0.9, "benchmark_equity_index": 1.1,
         "drawdown": -0.1, "below_assumed_maintenance": False},
        {"date": "2024-01-02", "equity_index": 0.8, "benchmark_equity_index": 1.2,
         "drawdown": -0.2, "below_assumed_maintenance": breaches},
        {"date": "2024-12-31", "equity_index": 0.95, "benchmark_equity_index": 1.3,
         "drawdown": -0.05, "below_assumed_maintenance": False},
    ]
    return {
        "method": "rolling_train_signal_t_minus_1_execute_t_close_hold_next_session",
        "data": {"symbols": ["A", "B"],
                 "first_backtest_return_date": "2023-12-29",
                 "last_backtest_return_date": "2024-12-31",
                 "backtest_daily_observations": 3},
        "parameters": {"target_leverage": leverage, "risk_aversion": 4.0,
                       "lookback_trading_days": 60,
                       "rebalance_every_trading_days": 10,
                       "max_weight": 0.6,
                       "trading_cost_basis_points_each_side": 5.0},
        "strategy": {"annualized_compounded_return": 0.08,
                     "annualized_volatility": 0.25,
                     "max_drawdown": -0.65,
                     "sharpe_zero_risk_free_assumption": 0.6,
                     "ending_equity_index": 0.95,
                     "below_assumed_maintenance_days": int(breaches)},
        "benchmark": {"type": "equal_weight_buy_and_hold_same_symbols_no_costs",
                      "annualized_compounded_return": 0.12, "max_drawdown": -0.5},
        "equity_curve": curve,
    }


class AuditTest(unittest.TestCase):
    def _write(self, folder: str, prefix: str, data: dict) -> Path:
        path = Path(folder) / (prefix + ".json")
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_valid_audit_flags_breach_and_underperformance(self):
        with tempfile.TemporaryDirectory() as folder:
            cash = self._write(folder, "cash", example_report(1.0))
            margin = self._write(folder, "margin", example_report(2.0, True))
            result = audit_backtests(cash, margin)
        self.assertEqual(result["observations"], 3)
        self.assertEqual(result["margin"]["maintenance_breach_dates"], ["2024-01-02"])
        self.assertTrue(result["margin"]["research_flags"]["margin_breach_detected"])
        self.assertTrue(result["cash"]["research_flags"]["underperformed_equal_weight_cagr"])
        self.assertFalse(result["conclusion"]["broker_execution_suitability_established"])
        self.assertAlmostEqual(result["cash"]["calendar_year_returns"][0]["return"], -0.1)
        self.assertAlmostEqual(
            result["cash"]["calendar_year_returns"][1]["return"],
            0.95 / 0.9 - 1
        )

    def test_cli_writes_complete_calendar_breakdown(self):
        with tempfile.TemporaryDirectory() as folder:
            cash = self._write(folder, "cash", example_report(1.0))
            margin = self._write(folder, "margin", example_report(2.0, True))
            output = Path(folder) / "risk_audit.json"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                code = main([
                    "audit", "--cash", str(cash),
                    "--margin", str(margin), "--output", str(output),
                ])
            self.assertEqual(code, 0)
            console = json.loads(printed.getvalue())
            self.assertEqual(console["written"], str(output))
            self.assertNotIn("calendar_year_returns", console["cash"])
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["cash"]["calendar_year_returns"]), 2)

    def test_rejects_changed_universe(self):
        with tempfile.TemporaryDirectory() as folder:
            cash_data = example_report(1.0)
            margin_data = example_report(2.0)
            margin_data["data"]["symbols"] = ["A", "C"]
            cash = self._write(folder, "cash", cash_data)
            margin = self._write(folder, "margin", margin_data)
            with self.assertRaisesRegex(ValueError, "symbols"):
                audit_backtests(cash, margin)

    def test_rejects_mismatched_curve_dates(self):
        with tempfile.TemporaryDirectory() as folder:
            cash = self._write(folder, "cash", example_report(1.0))
            margin_data = example_report(2.0)
            margin_data["equity_curve"][1]["date"] = "2024-01-03"
            margin = self._write(folder, "margin", margin_data)
            with self.assertRaisesRegex(ValueError, "identical date grids"):
                audit_backtests(cash, margin)

    def test_rejects_inconsistent_breach_count(self):
        with tempfile.TemporaryDirectory() as folder:
            cash = self._write(folder, "cash", example_report(1.0))
            margin_data = example_report(2.0, True)
            margin_data["strategy"]["below_assumed_maintenance_days"] = 0
            margin = self._write(folder, "margin", margin_data)
            with self.assertRaisesRegex(ValueError, "breaches disagree"):
                audit_backtests(cash, margin)

    def test_rejects_wrong_leverage_class(self):
        with tempfile.TemporaryDirectory() as folder:
            cash = self._write(folder, "cash", example_report(2.0))
            margin = self._write(folder, "margin", example_report(2.0))
            with self.assertRaisesRegex(ValueError, "Cash comparison"):
                audit_backtests(cash, margin)

    def test_rejects_mismatched_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            cash = self._write(folder, "cash", example_report(1.0))
            margin_data = example_report(2.0)
            margin_data["parameters"]["risk_aversion"] = 6.0
            margin = self._write(folder, "margin", margin_data)
            with self.assertRaisesRegex(ValueError, "risk_aversion"):
                audit_backtests(cash, margin)


if __name__ == "__main__":
    unittest.main()
