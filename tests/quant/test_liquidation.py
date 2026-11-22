"""Regression tests for opt-in NEXT-close deleveraging (not real broker behavior)."""

import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.backtest import backtest_walk_forward


def make_crash_prices(folder: str) -> Path:
    path = Path(folder) / "crash.csv"
    first = date(2021, 1, 1)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "symbol", "adjusted_close"])
        for day in range(128):
            trading_date = (first + timedelta(days=day)).isoformat()
            shock = 0.60 if day >= 75 else 1.0
            writer.writerow([trading_date, "A", (100 + .20 * day + .30 * (day % 3)) * shock])
            writer.writerow([trading_date, "B", (100 + .15 * day + .25 * (day % 5)) * shock])
    return path


class MarginPolicyTest(unittest.TestCase):
    def test_trigger_is_next_close_then_remaining_unlevered(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = make_crash_prices(folder)
            baseline = backtest_walk_forward(
                path, lookback_days=40, rebalance_days=21, leverage=2,
                fee_bps=5, margin_policy="none"
            )
            protected = backtest_walk_forward(
                path, lookback_days=40, rebalance_days=21, leverage=2,
                fee_bps=5, margin_policy="next_close_deleverage_to_1x"
            )
        self.assertGreater(baseline["strategy"]["below_assumed_maintenance_days"], 0)
        events = protected["forced_deleveraging_events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(protected["strategy"]["forced_deleveraging_event_count"], 1)
        self.assertLess(events[0]["trigger_date"], events[0]["execution_date"])
        self.assertAlmostEqual(events[0]["debt_after_equity_index_units"], 0.0)
        self.assertEqual(protected["strategy"]["final_effective_target_leverage"], 1.0)
        self.assertTrue(any("Fixed current symbol universe" in warning for warning in protected["warnings"]))
        self.assertTrue(any("final day triggers liquidation" in warning for warning in protected["warnings"]))
        self.assertFalse(any("pending.Fixed" in warning for warning in protected["warnings"]))
        self.assertGreater(
            protected["strategy"]["total_trading_fees_equity_index_units"],
            0
        )

    def test_default_preserves_baseline_results(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = make_crash_prices(folder)
            default = backtest_walk_forward(
                path, lookback_days=40, rebalance_days=21, leverage=2, fee_bps=0
            )
            explicit = backtest_walk_forward(
                path, lookback_days=40, rebalance_days=21,
                leverage=2, fee_bps=0, margin_policy="none"
            )
        self.assertEqual(
            default["strategy"]["ending_equity_index"],
            explicit["strategy"]["ending_equity_index"]
        )
        self.assertEqual(default["strategy"]["forced_deleveraging_event_count"], 0)

    def test_invalid_policy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = make_crash_prices(folder)
            with self.assertRaisesRegex(ValueError, "margin_policy"):
                backtest_walk_forward(path, lookback_days=40, margin_policy="broker_real")
            with self.assertRaisesRegex(ValueError, "requires leverage"):
                backtest_walk_forward(
                    path, lookback_days=40, margin_policy="next_close_deleverage_to_1x",
                    leverage=1
                )


if __name__ == "__main__":
    unittest.main()
