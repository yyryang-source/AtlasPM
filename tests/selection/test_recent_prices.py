"""Synthetic regression checks for bounded-memory stock-rank inference.

No internet, trained model, broker or downloaded market data is required.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.quant.historical import load_adjusted_prices
from atlaspm.selection.ai_stock_ranker import _features, _history_arrays
from atlaspm.selection.recent_prices import load_recent_adjusted_prices


class RecentPriceInferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "prices.csv"
        self.days = [date(2023, 1, 1) + timedelta(days=i) for i in range(410)]
        self.cutoff = self.days[350]

    def write(self, rows):
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "symbol", "adjusted_close"])
            writer.writerows(rows)

    def fixture(self, *, date_major):
        rows = []
        if date_major:
            for i, day in enumerate(self.days):
                for j, symbol in enumerate(("AAA", "BBB", "CCC")):
                    rows.append((day.isoformat(), symbol, f"{100 + i * (j+1) * .04:.9f}"))
        else:
            for j, symbol in enumerate(("AAA", "BBB", "CCC")):
                for i, day in enumerate(self.days):
                    rows.append((day.isoformat(), symbol, f"{100 + i * (j+1) * .04:.9f}"))
        self.write(rows)

    def assert_matches_full_loader(self):
        original = load_adjusted_prices(self.path)
        expected = {
            symbol: dict(sorted(
                ((d, p) for d, p in history.items() if d <= self.cutoff)
            )[-253:]) for symbol, history in original.items()
        }
        recent = load_recent_adjusted_prices(self.path, as_of=self.cutoff)
        self.assertEqual(recent, expected)
        self.assertTrue(all(len(x) <= 253 for x in recent.values()))
        expected_arrays = _history_arrays(expected)
        recent_arrays = _history_arrays(recent)
        for symbol in expected:
            self.assertEqual(
                _features(expected_arrays[symbol], self.cutoff),
                _features(recent_arrays[symbol], self.cutoff),
            )

    def test_symbol_major_streaming_matches_full_history(self):
        self.fixture(date_major=False)
        with patch(
            "atlaspm.selection.recent_prices.load_adjusted_prices",
            side_effect=AssertionError("Unexpected full-history fallback"),
        ):
            self.assert_matches_full_loader()

    def test_date_major_streaming_matches_full_history(self):
        self.fixture(date_major=True)
        with patch(
            "atlaspm.selection.recent_prices.load_adjusted_prices",
            side_effect=AssertionError("Unexpected full-history fallback"),
        ):
            self.assert_matches_full_loader()

    def test_out_of_order_row_falls_back_without_changing_scores(self):
        self.write([
            ("2024-01-03", "AAA", 12),
            ("2024-01-01", "AAA", 10),
            ("2024-01-02", "AAA", 11),
            ("2024-01-02", "BBB", 23),
        ])
        with patch(
            "atlaspm.selection.recent_prices.load_adjusted_prices",
            wraps=load_adjusted_prices,
        ) as fallback:
            self.assert_matches_full_loader()
            self.assertEqual(fallback.call_count, 1)

    def test_duplicate_in_discarded_old_window_is_not_silent(self):
        self.fixture(date_major=False)
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow((self.days[4].isoformat(), "AAA", 201))
        with self.assertRaisesRegex(ValueError, "Duplicate price"):
            load_recent_adjusted_prices(self.path, as_of=self.cutoff)

    def test_future_prices_are_not_used_and_are_still_validated(self):
        self.write([
            ("2025-01-02", "FUTURE", 120),
            ("2024-01-02", "AAA", 10),
            ("2024-01-03", "AAA", 11),
        ])
        recent = load_recent_adjusted_prices(
            self.path, as_of=date(2024, 1, 3)
        )
        self.assertEqual(recent["FUTURE"], {})
        self.assertEqual(len(recent["AAA"]), 2)
        # A bad *future* quote must still be rejected, just like the
        # original full loader. The ranker must not ignore corrupt input.
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(("2027-01-02", "BAD", -1))
        with self.assertRaisesRegex(ValueError, "Invalid symbol or adjusted_close"):
            load_recent_adjusted_prices(
                self.path, as_of=date(2024, 1, 3)
            )

    def test_empty_and_invalid_inputs_fail_closed(self):
        self.write([])
        with self.assertRaisesRegex(ValueError, "No valid price rows"):
            load_recent_adjusted_prices(self.path, as_of=self.cutoff)
        with self.assertRaisesRegex(ValueError, "datetime.date"):
            load_recent_adjusted_prices(self.path, as_of="2024-01-01")
        self.path.write_text("symbol,price\\nABC,1\\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "CSV needs columns"):
            load_recent_adjusted_prices(self.path, as_of=self.cutoff)


if __name__ == "__main__":
    unittest.main()
