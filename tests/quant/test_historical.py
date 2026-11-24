"""Offline tests for strict historical price data handling."""

import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.data.historical import estimate_from_prices, load_adjusted_prices


class HistoricalTest(unittest.TestCase):
    def _make_csv(self, rows):
        folder = tempfile.TemporaryDirectory()
        path = Path(folder.name) / "prices.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "symbol", "adjusted_close"])
            writer.writerows(rows)
        return folder, path

    def test_sample_estimates_and_alignment(self):
        start = date(2026, 1, 1)
        rows = []
        for i in range(66):
            d = (start + timedelta(days=i)).isoformat()
            rows.extend([(d, "A", 100 + 2 * i + 0.05 * (i % 3)),
                         (d, "B", 120 + i + 0.09 * (i % 5))])
        rows.append(((start + timedelta(days=66)).isoformat(), "A", 237))
        folder, path = self._make_csv(rows)
        with folder:
            result = estimate_from_prices(path, min_observations=60)
        self.assertEqual(result["metadata"]["return_observations"], 65)
        self.assertEqual(result["metadata"]["symbols"], ["A", "B"])
        self.assertEqual(len(result["covariance"]), 2)
        self.assertAlmostEqual(result["covariance"][0][1], result["covariance"][1][0])

    def test_duplicate_or_bad_data(self):
        for rows in (
            [("2026-01-01", "A", 2), ("2026-01-01", "A", 2)],
            [("2026-01-01", "A", -1)],
            [("not-date", "A", 2)],
        ):
            with self.subTest(rows=rows):
                folder, path = self._make_csv(rows)
                with folder, self.assertRaises(ValueError):
                    load_adjusted_prices(path)

    def test_not_enough_shared_dates(self):
        folder, path = self._make_csv([
            ("2026-01-01", "A", 10), ("2026-01-01", "B", 20),
            ("2026-01-02", "A", 11), ("2026-01-02", "B", 21),
        ])
        with folder, self.assertRaisesRegex(ValueError, "shared close dates"):
            estimate_from_prices(path, min_observations=3)

    def test_flat_prices_refused(self):
        start = date(2026, 1, 1)
        rows = [((start + timedelta(days=i)).isoformat(), "A", 10) for i in range(66)]
        folder, path = self._make_csv(rows)
        with folder, self.assertRaisesRegex(ValueError, "zero or invalid"):
            estimate_from_prices(path)


if __name__ == "__main__":
    unittest.main()
