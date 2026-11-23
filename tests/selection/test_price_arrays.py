"""Array-backed large-universe ranker ingestion: strict legacy parity."""

from __future__ import annotations

import csv
from datetime import date, timedelta
import tempfile
from pathlib import Path
import unittest

from atlaspm.historical import load_adjusted_prices
from atlaspm.selection.ai_stock_ranker import _history_arrays, create_training_rows
from atlaspm.selection.price_arrays import load_stock_price_arrays, load_recent_stock_price_arrays


class StockPriceArrayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "stocks.csv"

    def write(self, rows, *, header=True):
        with self.path.open("w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out)
            if header:
                writer.writerow(("date", "symbol", "adjusted_close"))
            writer.writerows(rows)

    def assert_legacy_parity(self):
        # Compare price values and every date; sorting and normalization
        # must be exactly what the previous generic loader produced.
        expected = _history_arrays(load_adjusted_prices(self.path))
        self.assertEqual(load_stock_price_arrays(self.path), expected)

    def test_ticker_major_and_date_major_have_identical_values(self):
        rows = [
            ("2026-01-02", " aapl ", "10.5"),
            ("2026-01-05", " aapl ", "11.5"),
            ("2026-01-02", "AMD", "20"),
            ("2026-01-05", "AMD", "19"),
            ("2026-01-02", "NVDA", "50"),
            ("2026-01-05", "NVDA", "60"),
        ]
        self.write(rows)
        first = load_stock_price_arrays(self.path)
        self.assert_legacy_parity()
        self.write(sorted(rows))
        self.assert_legacy_parity()
        self.assertEqual(load_stock_price_arrays(self.path), first)

    def test_shuffled_dates_and_nonadjacent_duplicates(self):
        rows = [
            ("2026-01-07", "AMD", 22),
            ("2026-01-02", "NVDA", 100),
            ("2026-01-05", "AMD", 21),
            ("2026-01-02", "AMD", 20),
            ("2026-01-05", "NVDA", 105),
        ]
        self.write(rows)
        self.assert_legacy_parity()
        data = load_stock_price_arrays(self.path)
        self.assertEqual(data["AMD"][1], [20.0, 21.0, 22.0])
        self.write(rows + [("2026-01-05", "AMD", 99)])
        with self.assertRaisesRegex(ValueError, "Duplicate price for AMD"):
            load_stock_price_arrays(self.path)

    def test_fails_closed_on_invalid_rows_and_empty_dataset(self):
        for rows in (
            [],
            [("2026-01-02", "AAPL", 0)],
            [("2026-01-02", "AAPL", -1)],
            [("2026-01-02", "AAPL", "nan")],
            [("2026-01-02", "", 12)],
            [("bad-date", "AAPL", 12)],
        ):
            with self.subTest(rows=rows):
                self.write(rows)
                with self.assertRaises(ValueError):
                    load_stock_price_arrays(self.path)

    def test_bounded_recent_history_matches_full_arrays_across_input_orders(self):
        days = []
        cursor = date(2024, 1, 2)
        while len(days) < 290:
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor += timedelta(days=1)
        cutoff = days[269]
        rows = []
        for i, day in enumerate(days):
            for j, symbol in enumerate(("AAPL", "AMD", "NVDA")):
                rows.append((day.isoformat(), symbol, 100 + i * (j + 1) * 0.2))
        # Future-only symbol is still counted in the original source universe,
        # but has no features at the current decision close.
        rows.append((days[-1].isoformat(), "NEW", 100))
        for ordering in ("date_major", "ticker_major", "shuffled"):
            with self.subTest(ordering=ordering):
                if ordering == "ticker_major":
                    candidate = sorted(rows, key=lambda r: (r[1], r[0]))
                elif ordering == "shuffled":
                    candidate = rows[::2] + rows[1::2]
                else:
                    candidate = rows
                self.write(candidate)
                full = load_stock_price_arrays(self.path)
                recent = load_recent_stock_price_arrays(self.path, as_of=cutoff)
                expected = {
                    symbol: (
                        [d for d in ds if d <= cutoff][-253:],
                        [px for d, px in zip(ds, prices) if d <= cutoff][-253:],
                    )
                    for symbol, (ds, prices) in full.items()
                }
                self.assertEqual(recent, expected)
                self.assertEqual(recent["AAPL"][0][-1], cutoff)
                self.assertEqual(len(recent["AAPL"][0]), 253)
                self.assertEqual(recent["NEW"], ([], []))

    def test_bounded_recent_history_ignores_future_values_but_validates_them(self):
        self.write([
            ("2026-01-02", "AAPL", 100),
            ("2026-01-05", "AAPL", 110),
            ("2026-01-06", "AAPL", 120),
        ])
        cutoff = date(2026, 1, 5)
        original = load_recent_stock_price_arrays(self.path, as_of=cutoff)
        self.write([
            ("2026-01-02", "AAPL", 100),
            ("2026-01-05", "AAPL", 110),
            ("2026-01-06", "AAPL", 999999),
        ])
        updated = load_recent_stock_price_arrays(self.path, as_of=cutoff)
        self.assertEqual(original, updated)
        self.write([
            ("2026-01-02", "AAPL", 100),
            ("2026-01-05", "AAPL", 110),
            ("2026-01-06", "AAPL", float("inf")),
        ])
        with self.assertRaisesRegex(ValueError, "Invalid symbol or adjusted_close"):
            load_recent_stock_price_arrays(self.path, as_of=cutoff)

    def test_bounded_recent_history_rejects_nonadjacent_duplicate(self):
        self.write([
            ("2026-01-02", "AMD", 50),
            ("2026-01-05", "AMD", 60),
            ("2026-01-03", "NVDA", 100),
            ("2026-01-02", "AMD", 70),
        ])
        with self.assertRaisesRegex(ValueError, "Duplicate price for AMD"):
            load_recent_stock_price_arrays(self.path, as_of=date(2026, 1, 5))

    def test_training_labels_match_reference_rows(self):
        # Reference path is deliberately independent of the new CSV loader.
        # Test next-close labels, dates and feature vectors unchanged.
        from atlaspm.selection.ai_stock_ranker import _candidate_rows
        bench_path = self.root / "benchmark.csv"
        days = []
        cursor = date(2023, 1, 2)
        while len(days) < 322:
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor += timedelta(days=1)
        rows = []
        for i, day in enumerate(days):
            for j, symbol in enumerate(("AAPL", "AMD", "NVDA")):
                rows.append((day.isoformat(), symbol,
                             100 * ((1 + 0.0002 + j * 0.00003) ** i)
                             * (1 + (i * (j + 3) % 11) / 3000)))
        self.write(rows)
        with bench_path.open("w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out)
            writer.writerow(("date", "symbol", "adjusted_close"))
            for i, day in enumerate(days):
                writer.writerow((day.isoformat(), "SPY",
                                 100 * (1.0001 ** i) * (1 + (i % 7) / 2000)))

        samples, meta = create_training_rows(
            self.path, bench_path, start=days[0].isoformat(),
            test_end=days[-1].isoformat(), sample_every_sessions=21,
            horizon_sessions=21,
        )
        legacy_histories = _history_arrays(load_adjusted_prices(self.path))
        benchmark = load_adjusted_prices(bench_path)["SPY"]
        reference = []
        end = len(days) - 2 - 21
        for index in range(252, end + 1, 21):
            reference.extend(_candidate_rows(
                legacy_histories, days, benchmark, index, horizon=21,
            ))
        self.assertEqual(samples, reference)
        self.assertEqual(meta["source_stock_universe"], 3)


if __name__ == "__main__":
    unittest.main()
