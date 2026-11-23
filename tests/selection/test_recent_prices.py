"""Regression tests: streaming inference exactly equals full array loading."""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.selection.ai_stock_ranker import _features
from atlaspm.selection.price_arrays import load_stock_price_arrays
from atlaspm.selection.recent_prices import load_recent_stock_price_arrays


class RecentPriceInferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "prices.csv"
        self.days = [date(2023, 1, 1) + timedelta(days=i) for i in range(410)]
        self.cutoff = self.days[350]

    def write(self, rows):
        with self.path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "symbol", "adjusted_close"])
            writer.writerows(rows)

    def fixture(self, *, date_major):
        rows = []
        if date_major:
            for i, day in enumerate(self.days):
                for j, symbol in enumerate(("AAA", "BBB", "CCC")):
                    rows.append((
                        day.isoformat(), symbol,
                        f"{100 + i * (j + 1) * .04:.9f}",
                    ))
        else:
            for j, symbol in enumerate(("AAA", "BBB", "CCC")):
                for i, day in enumerate(self.days):
                    rows.append((
                        day.isoformat(), symbol,
                        f"{100 + i * (j + 1) * .04:.9f}",
                    ))
        self.write(rows)

    def assert_matches_full_arrays(self):
        full = load_stock_price_arrays(self.path)
        expected = {}
        for symbol, (days, prices) in full.items():
            trailing = [
                (day, value) for day, value in zip(days, prices)
                if day <= self.cutoff
            ][-253:]
            expected[symbol] = (
                [day for day, _ in trailing],
                [value for _, value in trailing],
            )
        recent = load_recent_stock_price_arrays(
            self.path, as_of=self.cutoff
        )
        self.assertEqual(recent, expected)
        self.assertTrue(all(len(days) <= 253 for days, _ in recent.values()))
        for symbol in recent:
            self.assertEqual(
                _features(recent[symbol], self.cutoff),
                _features(expected[symbol], self.cutoff),
            )

    def test_ticker_major_fast_path_matches_full(self):
        self.fixture(date_major=False)
        with patch(
            "atlaspm.selection.recent_prices.load_stock_price_arrays",
            side_effect=AssertionError("Expected streaming fast path"),
        ):
            self.assert_matches_full_arrays()

    def test_date_major_fast_path_matches_full(self):
        self.fixture(date_major=True)
        with patch(
            "atlaspm.selection.recent_prices.load_stock_price_arrays",
            side_effect=AssertionError("Expected streaming fast path"),
        ):
            self.assert_matches_full_arrays()

    def test_unordered_falls_back_and_preserves_scores(self):
        self.write([
            ("2024-01-03", "AAA", 12),
            ("2024-01-01", "AAA", 10),
            ("2024-01-02", "AAA", 11),
            ("2024-01-02", "BBB", 23),
        ])
        self.cutoff = date(2024, 1, 3)
        with patch(
            "atlaspm.selection.recent_prices.load_stock_price_arrays",
            wraps=load_stock_price_arrays,
        ) as fallback:
            self.assert_matches_full_arrays()
            self.assertEqual(fallback.call_count, 1)

    def test_duplicate_too_old_for_window_rejected(self):
        self.fixture(date_major=False)
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                (self.days[4].isoformat(), "AAA", 201)
            )
        with self.assertRaisesRegex(ValueError, "Duplicate price"):
            load_recent_stock_price_arrays(
                self.path, as_of=self.cutoff
            )

    def test_future_records_excluded_but_invalid_future_rejected(self):
        self.write([
            ("2025-01-02", "FUTURE", 120),
            ("2024-01-02", "AAA", 10),
            ("2024-01-03", "AAA", 11),
        ])
        cutoff = date(2024, 1, 3)
        result = load_recent_stock_price_arrays(self.path, as_of=cutoff)
        self.assertEqual(result["FUTURE"], ([], []))
        self.assertEqual(result["AAA"][1], [10, 11])
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(("2027-01-02", "BAD", -1))
        with self.assertRaisesRegex(ValueError, "Invalid symbol or adjusted_close"):
            load_recent_stock_price_arrays(self.path, as_of=cutoff)

    def test_missing_and_empty_inputs_fail_closed(self):
        self.write([])
        with self.assertRaisesRegex(ValueError, "No valid price rows"):
            load_recent_stock_price_arrays(self.path, as_of=self.cutoff)
        with self.assertRaisesRegex(ValueError, "datetime.date"):
            load_recent_stock_price_arrays(
                self.path, as_of="2024-01-01"
            )
        self.path.write_text("symbol,price\nABC,1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "CSV needs columns"):
            load_recent_stock_price_arrays(self.path, as_of=self.cutoff)


if __name__ == "__main__":
    unittest.main()
