"""Source joins for historical membership and cash exit research."""

import csv
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atlaspm.historical import load_adjusted_prices
from atlaspm.selection.historical_stock_imports import import_cash_exits, import_membership_spans
from atlaspm.selection.historical_universe import HistoricalUniverse
from atlaspm.selection.stock_delistings import load_stock_delistings


class HistoricalStockImportsTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _write(self, name, contents):
        path = self.root / name
        path.write_text(contents, encoding="utf-8")
        return path

    def test_membership_boundaries_follow_benchmark_sessions(self):
        index = self._write("index.csv", "date,symbol,adjusted_close\n"
                                        "2024-01-05,SPY,100\n2024-01-08,SPY,100\n2024-01-09,SPY,100\n")
        members = self._write("spans.csv", "ticker,cik,start,end,start_confidence\n"
                                          "OLD,123,2023-12-01,2024-01-08,exact\n"
                                          "NEW,456,2024-01-06,,snapshot\n"
                                          "KEEP,789,2023-12-01,,left_censored\n")
        output = self.root / "membership.csv"
        report = import_membership_spans(members, index, date(2024, 1, 5), date(2024, 1, 9), output)
        universe = HistoricalUniverse(output)
        self.assertEqual(universe.members_at(date(2024, 1, 5))[1], ["KEEP", "OLD"])
        self.assertEqual(universe.members_at(date(2024, 1, 8))[1], ["KEEP", "NEW"])
        self.assertEqual(report["member_days_by_start_confidence"], {"exact": 1, "left_censored": 3, "snapshot": 2})

    def test_cash_exit_carries_return_from_raw_quote_onto_adjusted_basis(self):
        adjusted = self._write("adj.csv", "date,symbol,adjusted_close\n2024-01-05,MERGER,20\n")
        raw = self._write("raw.csv", "date,symbol,close\n2024-01-05,MERGER,40\n")
        events = self._write("events.csv", "symbol,last_trade_date,cash_date,cash_per_share,source_ref\n"
                                         "MERGER,2024-01-05,2024-01-08,44,test_disclosure\n")
        result = self.root / "delistings.csv"
        self.assertEqual(import_cash_exits(events, raw, adjusted, result)["events"], 1)
        schedule = [date(2024, 1, 5), date(2024, 1, 8)]
        exit_terms = load_stock_delistings(result, load_adjusted_prices(adjusted), schedule)["MERGER"]
        self.assertAlmostEqual(exit_terms.last_close * (1 + exit_terms.return_from_last_close), 22)
        with result.open(newline="", encoding="utf-8") as handle:
            self.assertEqual(next(csv.DictReader(handle))["source_ref"], "test_disclosure")

    def test_cash_exit_rejects_quoted_prices_after_delisting(self):
        adjusted = self._write("adj.csv", "date,symbol,adjusted_close\n2024-01-05,MERGER,20\n2024-01-08,MERGER,21\n")
        raw = self._write("raw.csv", "date,symbol,close\n2024-01-05,MERGER,40\n")
        events = self._write("events.csv", "symbol,last_trade_date,cash_date,cash_per_share,source_ref\n"
                                         "MERGER,2024-01-05,2024-01-08,44,test_disclosure\n")
        result = self.root / "delistings.csv"
        with self.assertRaisesRegex(ValueError, "must end on its last trade date"):
            import_cash_exits(events, raw, adjusted, result)
        self.assertFalse(result.exists())
