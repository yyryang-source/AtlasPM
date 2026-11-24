"""Offline walk-forward tests for leakage, costs, debt, and date alignment."""

import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.engine.backtest import backtest_walk_forward


def sample_csv(folder, change_future=False, missing=False):
    path = Path(folder) / "prices.csv"
    first = date(2021, 1, 1)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "symbol", "adjusted_close"])
        for day in range(128):
            d = (first + timedelta(days=day)).isoformat()
            a = 100 + day * .1 + (day % 3) * .2
            b = 120 + day * .3 + (day % 7) * .4
            if change_future and day == 127:
                b = b * 2
            writer.writerow([d, "A", a])
            if not (missing and day == 120):
                writer.writerow([d, "B", b])
    return path


class WalkForwardTests(unittest.TestCase):
    def test_backtest_metrics_and_weights(self):
        with tempfile.TemporaryDirectory() as temp:
            report = backtest_walk_forward(
                sample_csv(temp), lookback_days=40, rebalance_days=21, fee_bps=5
            )
        self.assertEqual(report["data"]["backtest_daily_observations"], 86)
        self.assertEqual(report["strategy"]["rebalance_count"], 5)
        self.assertGreater(report["strategy"]["total_trading_fees_equity_index_units"], 0)
        self.assertEqual(
            report["rebalances"][0]["first_held_return_date"],
            report["equity_curve"][0]["date"]
        )
        self.assertLess(
            report["rebalances"][0]["training_last_date"],
            report["rebalances"][0]["execution_date"]
        )
        self.assertLess(
            report["rebalances"][0]["execution_date"],
            report["rebalances"][0]["first_held_return_date"]
        )
        self.assertAlmostEqual(sum(report["rebalances"][0]["weights"].values()), 1)
        self.assertGreater(report["strategy"]["ending_equity_index"], 0)

    def test_no_future_lookahead_first_rebalance(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = backtest_walk_forward(
                sample_csv(a), lookback_days=40, rebalance_days=21
            )
            future_changed = backtest_walk_forward(
                sample_csv(b, change_future=True), lookback_days=40,
                rebalance_days=21
            )
        self.assertEqual(
            first["rebalances"][0]["weights"],
            future_changed["rebalances"][0]["weights"]
        )
        self.assertNotEqual(
            first["strategy"]["ending_equity_index"],
            future_changed["strategy"]["ending_equity_index"]
        )

    def test_increasing_fees_reduces_final_value(self):
        with tempfile.TemporaryDirectory() as temp:
            path = sample_csv(temp)
            free = backtest_walk_forward(
                path, lookback_days=40, rebalance_days=21, fee_bps=0
            )
            fees = backtest_walk_forward(
                path, lookback_days=40, rebalance_days=21, fee_bps=30
            )
        self.assertLess(
            fees["strategy"]["ending_equity_index"],
            free["strategy"]["ending_equity_index"]
        )

    def test_margin_financing_and_maintenance(self):
        with tempfile.TemporaryDirectory() as temp:
            leveraged = backtest_walk_forward(
                sample_csv(temp), lookback_days=40, rebalance_days=21,
                leverage=2, fee_bps=0, annual_financing_rate=.09
            )
        self.assertGreater(
            leveraged["strategy"]["total_financing_interest_equity_index_units"], 0
        )
        self.assertEqual(
            leveraged["strategy"]["below_assumed_maintenance_days"], 0
        )

    def test_invalid_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            path = sample_csv(temp)
            for kw in (
                {"lookback_days": 0},
                {"rebalance_days": 0},
                {"fee_bps": -1},
                {"leverage": 5},
                {"max_weight": .4},
                {"maintenance_fraction": 0},
            ):
                with self.subTest(kw=kw), self.assertRaises(ValueError):
                    backtest_walk_forward(path, **({"lookback_days": 40} | kw))

    def test_common_dates_only(self):
        with tempfile.TemporaryDirectory() as temp:
            report = backtest_walk_forward(
                sample_csv(temp, missing=True), lookback_days=40,
                rebalance_days=21
            )
        self.assertEqual(report["data"]["shared_price_dates"], 127)


if __name__ == "__main__":
    unittest.main()
