"""Offline regression tests for next-close walk-forward stock screens."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.stock_walkforward import backtest_stock_screener


class StockWalkforwardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.stocks = self.base / "stocks.csv"
        self.benchmark = self.base / "benchmark.csv"
        self.dates = []
        day = date(2024, 1, 1)
        while len(self.dates) < 370:
            if day.weekday() < 5:
                self.dates.append(day)
            day += timedelta(days=1)
        self.start = self.dates[260].isoformat()
        self.end = self.dates[355].isoformat()
        self._write_prices()

    def _write_prices(self, missing=None):
        with self.stocks.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "symbol", "adjusted_close"])
            for i, day in enumerate(self.dates):
                for symbol, value in [
                    ("A", 100 * 1.001 ** i),
                    ("B", 70 * 1.0002 ** i),
                    ("C", 90 * 0.9998 ** i),
                ]:
                    if (symbol, day) != missing:
                        writer.writerow([day.isoformat(), symbol, value])
        with self.benchmark.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "symbol", "adjusted_close"])
            for i, day in enumerate(self.dates):
                writer.writerow([day.isoformat(), "SPY", 200 * 1.0003 ** i])

    def _run(self, **kwargs):
        arguments = {"start": self.start, "end": self.end, "top": 2, **kwargs}
        return backtest_stock_screener(self.stocks, self.benchmark, **arguments)

    def test_date_range_and_benchmark(self):
        r = self._run()
        self.assertEqual(r["actual_window"], {"start": self.start, "end": self.end})
        self.assertEqual(r["decision_count"], 5)
        self.assertEqual(r["executed_rebalances"], 5)
        self.assertEqual(r["equity_curve"][0]["equity_index"], 1.0)
        self.assertEqual(r["equity_curve"][0]["spy_index"], 1.0)
        self.assertGreater(r["benchmark"]["total_return"], 0)
        self.assertLessEqual(r["portfolio"]["max_drawdown"], 0)
        self.assertFalse(r["live_execution_enabled"])
        self.assertEqual(sum(v["sessions"] for v in r["yearly_returns"]),
                         len(r["equity_curve"]) - 1)

    def test_next_close_execution_no_same_close_profit(self):
        result = self._run(rebalance_days=1000, fee_bps=0, slippage_bps=0)
        self.assertEqual(result["executions"][0]["decision_date"], self.start)
        self.assertEqual(result["executions"][0]["execution_date"],
                         self.dates[261].isoformat())
        self.assertEqual(result["equity_curve"][0]["equity_index"], 1)
        self.assertAlmostEqual(result["equity_curve"][1]["equity_index"], 1)
        self.assertGreater(result["equity_curve"][2]["equity_index"], 1)

    def test_turnover_costs_reduce_performance(self):
        free = self._run(fee_bps=0, slippage_bps=0)
        paid = self._run(fee_bps=10, slippage_bps=10)
        self.assertGreater(free["portfolio"]["total_return"],
                           paid["portfolio"]["total_return"])
        self.assertGreater(paid["execution_summary"]["total_modeled_transaction_cost_as_fraction_of_initial_equity"], 0)
        self.assertEqual(free["execution_summary"]["total_modeled_transaction_cost_as_fraction_of_initial_equity"], 0)

    def test_future_rows_cannot_change_past_backtest(self):
        previous = self._run()
        future = (self.dates[-1] + timedelta(days=1)).isoformat()
        with self.stocks.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([future, "A", 999999999])
            writer.writerow([future, "B", 1])
        with self.benchmark.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([future, "SPY", 999999999])
        after = self._run()
        self.assertEqual(previous["equity_curve"], after["equity_curve"])
        self.assertEqual(previous["decisions"], after["decisions"])
        self.assertEqual(previous["portfolio"], after["portfolio"])

    def test_missing_execution_price_fails_closed(self):
        self._write_prices(missing=("A", self.dates[261]))
        with self.assertRaisesRegex(ValueError, "MISSING_EXECUTION_PRICE"):
            self._run()

    def test_missing_held_price_fails_closed(self):
        self._write_prices(missing=("A", self.dates[263]))
        with self.assertRaisesRegex(ValueError, "MISSING_HELD_PRICE"):
            self._run()

    def test_input_validation(self):
        with self.assertRaisesRegex(ValueError, "end must be later"):
            self._run(start="2028-01-01")
        with self.assertRaisesRegex(ValueError, "rebalance-days"):
            self._run(rebalance_days=0)
        with self.assertRaisesRegex(ValueError, "fee-bps"):
            self._run(fee_bps=-1)

    def test_cli_stores_private_json_only(self):
        from atlaspm.cli import main
        cwd = Path.cwd()
        try:
            os.chdir(self.base)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main(["stock-backtest", "--prices", str(self.stocks),
                             "--benchmark-prices", str(self.benchmark),
                             "--start", self.start, "--end", self.end,
                             "--top", "2", "--output", "local_data/walkforward.json"])
            self.assertEqual(code, 0)
            summary = json.loads(stream.getvalue())
            saved = json.loads((self.base / "local_data/walkforward.json").read_text())
            self.assertEqual(summary["decision_count"], saved["decision_count"])
            self.assertEqual(summary["portfolio"]["cagr"], saved["portfolio"]["cagr"])
            self.assertFalse(saved["live_execution_enabled"])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                status = main(["stock-backtest", "--prices", str(self.stocks),
                               "--benchmark-prices", str(self.benchmark),
                               "--start", self.start, "--end", self.end,
                               "--output", "public_results.json"])
            self.assertEqual(status, 2)
            self.assertFalse((self.base / "public_results.json").exists())
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
