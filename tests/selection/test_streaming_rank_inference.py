"""Bounded-memory 5k-stock inference parity and input-safety regression tests."""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest

from atlaspm.selection.ai_stock_ranker import (
    _candidate_rows, _history_arrays, _recent_inference_histories,
)
from atlaspm.quant.historical import load_adjusted_prices


class StreamingInferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "large_stocks.csv"
        self.days = []
        cursor = date(2024, 1, 1)
        while len(self.days) < 320:
            if cursor.weekday() < 5:
                self.days.append(cursor)
            cursor += timedelta(days=1)
        self.cutoff = self.days[300]
        self.symbols = ["AAPL", "NVDA", "AMD", "GOOGL", "JPM", "TSLA"]

    def write(self, *, date_major: bool = True, future_multiplier: float = 1.0):
        rows = []
        for day_index, d in enumerate(self.days):
            for i, name in enumerate(self.symbols + ["SPY"]):
                price = (50 + 4 * i) * (1 + 0.0002 * (i + 1)) ** day_index
                price *= 1 + ((day_index * (i + 2)) % 13) / 10000
                if d > self.cutoff:
                    price *= future_multiplier
                rows.append((d.isoformat(), name, price))
        if not date_major:
            rows.sort(key=lambda row: (row[1], row[0]))
        # These names exist in the source universe but may not be scored.
        rows.extend([
            (self.days[-1].isoformat(), "FUTURE", 50),
            (self.days[-2].isoformat(), "NEW", 55),
        ] if date_major else [
            (self.days[-2].isoformat(), "NEW", 55),
        ])
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "symbol", "adjusted_close"])
            writer.writerows(rows)

    def old_candidates(self):
        prices = load_adjusted_prices(self.path)
        prices.pop("SPY", None)
        bench = {d: 100.0 for d in self.days}
        return _candidate_rows(
            _history_arrays(prices), self.days[:301], bench, 300, horizon=None
        )

    def new_candidates(self):
        history = _recent_inference_histories(
            self.path, self.cutoff, exclude_symbol="SPY"
        )
        bench = {d: 100.0 for d in self.days}
        rows = _candidate_rows(
            history, self.days[:301], bench, 300, horizon=None
        )
        return rows, history

    def test_date_major_and_ticker_major_match_full_history(self):
        for date_major in (True, False):
            with self.subTest(date_major=date_major):
                self.write(date_major=date_major)
                previous = self.old_candidates()
                current, history = self.new_candidates()
                self.assertEqual(previous, current)
                self.assertEqual(len(current), len(self.symbols))
                self.assertNotIn("SPY", history)
                self.assertTrue(all(len(dates) <= 253 for dates, _ in history.values()))
                self.assertEqual(len(history["AAPL"][0]), 253)
                self.assertEqual(history["AAPL"][0][-1], self.cutoff)
                self.assertIn("FUTURE", history) if date_major else None

    def test_future_quote_changes_do_not_change_decision_features(self):
        self.write()
        before, _ = self.new_candidates()
        self.write(future_multiplier=100000)
        after, _ = self.new_candidates()
        self.assertEqual(after, before)

    def test_nonchronological_or_duplicate_ticker_rows_fail_closed(self):
        self.write()
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow([self.cutoff.isoformat(), "AAPL", 20])
        with self.assertRaisesRegex(ValueError, "nonchronological"):
            self.new_candidates()

    def test_invalid_future_quotes_are_not_silently_ignored(self):
        self.write()
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow(["2026-01-01", "CORRUPT", "-1"])
        with self.assertRaisesRegex(ValueError, "Invalid symbol or adjusted_close"):
            self.new_candidates()

    def test_invalid_header_fails_before_allocation(self):
        self.path.write_text("day,ticker,price\\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "CSV needs columns"):
            self.new_candidates()


if __name__ == "__main__":
    unittest.main()
