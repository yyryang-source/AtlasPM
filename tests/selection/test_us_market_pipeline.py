"""Offline tests for automatic US listing discovery and cached all-market quotes."""

from __future__ import annotations

import contextlib
import csv
from datetime import date, timedelta
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from atlaspm.us_market_universe import build_us_listed_universe, read_market_universe
from atlaspm.us_market_prices import sync_us_market_prices
from atlaspm.stock_screener import screen_stocks


NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
NVDA|NVIDIA Corporation - Common Stock|Q|N|N|100|N|N
GOOD|Ordinary Company, Inc. - Ordinary Shares|G|N|N|100|N|N
FUND|Some ETF|G|N|N|100|Y|N
WRNT|Example Corp - Warrant|S|N|N|100|N|N
TST|Tester - Common Stock|Q|Y|N|100|N|N
UNKNOWN|Ambiguous Corporation|G|N|N|100|N|N
File Creation Time: 1122202612:00|||||||
"""
OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
JPM|JPMorgan Chase & Co. Common Stock|N|JPM|N|100|N|JPM
BRK.B|Berkshire Hathaway Class B Common Stock|N|BRK.B|N|100|N|BRK/B
AAPL|Apple duplicate Common Stock|N|AAPL|N|100|N|AAPL
XLK|Sector Fund ETF|P|XLK|Y|100|N|XLK
TEST|Testing Common Stock|N|TEST|N|100|Y|TEST
PREF|Example 5% Preferred Stock|N|PREF|N|100|N|PREF
File Creation Time: 1122202612:00|||||||
"""


class USMarketPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.nasdaq = self.root / "nasdaqlisted.txt"
        self.other = self.root / "otherlisted.txt"
        self.nasdaq.write_text(NASDAQ, encoding="utf-8")
        self.other.write_text(OTHER, encoding="utf-8")
        self.universe = self.root / "universe.csv"
        self.cache = self.root / "ticker_cache"
        self.prices = self.root / "market.csv"

    def _discover(self):
        return build_us_listed_universe(
            self.universe, nasdaq_file=self.nasdaq, other_file=self.other
        )

    @staticmethod
    def _fake_prices(symbol, start, end):
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
        today = first
        i = 0
        rows = []
        while today < last:
            if today.weekday() < 5:
                rows.append((today.isoformat(), 30 + i * (0.12 if symbol == "AAPL" else 0.09)))
                i += 1
            today += timedelta(days=1)
        return rows

    def _sync(self, **kwargs):
        return sync_us_market_prices(
            self.universe, self.cache, self.prices,
            start="2024-01-01", end="2025-02-01",
            downloader=self._fake_prices, **kwargs,
        )

    def test_discover_and_filter_all_exchanges(self):
        result = self._discover()
        rows = read_market_universe(self.universe)
        symbols = {r["listing_symbol"] for r in rows}
        self.assertEqual(symbols, {"AAPL", "NVDA", "GOOD", "UNKNOWN", "JPM", "BRK.B"})
        self.assertEqual(result["listing_count"], 6)
        self.assertEqual(result["deduplicated_listing_count"], 1)
        self.assertEqual(result["unverified_issue_count"], 1)
        self.assertEqual(next(r for r in rows if r["listing_symbol"] == "BRK.B")["provider_symbol"], "BRK-B")
        self.assertFalse(result["live_execution_enabled"])

    def test_empty_or_bad_directory_rejected(self):
        self.nasdaq.write_text("anything|else\nA|B", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Incorrect nasdaq"):
            self._discover()
        self.nasdaq.write_text(NASDAQ.splitlines()[0] + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no securities"):
            self._discover()

    def test_quote_sync_covers_all_tickers_and_screens(self):
        self._discover()
        r = self._sync(workers=2)
        self.assertEqual(r["listing_candidates"], 6)
        self.assertEqual(r["cached_or_downloaded_symbols"], 6)
        self.assertEqual(r["failed_this_run"], 0)
        self.assertTrue(r["full_market_coverage"])
        self.assertEqual(r["missing_symbols"], 0)
        scores = screen_stocks(self.prices, "2025-01-31", top=10)
        self.assertEqual(scores["universe_count"], 6)
        self.assertEqual(scores["eligible_count"], 6)
        self.assertFalse(scores["live_execution_enabled"])

    def test_partial_and_resume_skip_successful_cache(self):
        self._discover()
        counter: dict[str, int] = {}
        def tracked(symbol, start, end):
            counter[symbol] = counter.get(symbol, 0) + 1
            return self._fake_prices(symbol, start, end)
        first = sync_us_market_prices(
            self.universe, self.cache, self.prices,
            start="2024-01-01", end="2025-02-01",
            max_symbols=2, downloader=tracked, workers=1,
        )
        self.assertEqual(first["cached_or_downloaded_symbols"], 2)
        self.assertEqual(first["missing_symbols"], 4)
        self.assertFalse(first["full_market_coverage"])
        second = sync_us_market_prices(
            self.universe, self.cache, self.prices,
            start="2024-01-01", end="2025-02-01",
            downloader=tracked, workers=1,
        )
        self.assertEqual(second["cached_or_downloaded_symbols"], 6)
        self.assertEqual(second["new_download_successes"], 4)
        self.assertEqual(len(counter), 6)
        self.assertTrue(all(count == 1 for count in counter.values()))

    def test_failed_ticker_is_reported_not_claimed_covered(self):
        self._discover()
        def bad(symbol, start, end):
            if symbol == "NVDA":
                raise RuntimeError("provider refused quote")
            return self._fake_prices(symbol, start, end)
        r = sync_us_market_prices(
            self.universe, self.cache, self.prices,
            start="2024-01-01", end="2025-02-01",
            downloader=bad, attempts=1,
        )
        self.assertEqual(r["failed_this_run"], 1)
        self.assertEqual(r["missing_symbols"], 1)
        self.assertIn("NVDA", r["failures"])
        self.assertFalse(r["full_market_coverage"])
        fixed = self._sync(attempts=1)
        self.assertEqual(fixed["new_download_successes"], 1)
        self.assertTrue(fixed["full_market_coverage"])

    def test_cache_only_valid_for_same_period(self):
        self._discover()
        self._sync()
        changed = sync_us_market_prices(
            self.universe, self.cache, self.prices,
            start="2024-01-01", end="2025-02-15",
            downloader=self._fake_prices, attempts=1,
        )
        self.assertEqual(changed["new_download_successes"], 6)

    def test_cli_private_output_and_offline_fixture(self):
        from atlaspm.cli import main
        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                success = main([
                    "stock-universe",
                    "--nasdaq-file", str(self.nasdaq),
                    "--other-file", str(self.other),
                    "--output", "local_data/listings.csv",
                    "--report", "local_data/listings_report.json",
                ])
            self.assertEqual(success, 0)
            self.assertEqual(json.loads(stdout.getvalue())["listing_count"], 6)
            self.assertTrue((self.root / "local_data/listings.csv").exists())
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                failed = main([
                    "stock-universe",
                    "--nasdaq-file", str(self.nasdaq),
                    "--other-file", str(self.other),
                    "--output", "public_listings.csv",
                ])
            self.assertEqual(failed, 2)
            self.assertFalse((self.root / "public_listings.csv").exists())
        finally:
            os.chdir(cwd)

    def test_cli_sync_from_preseeded_cache(self):
        from atlaspm.cli import main
        self._discover()
        self._sync()
        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = main([
                    "stock-sync-market", "--universe", str(self.universe),
                    "--cache-dir", str(self.cache),
                    "--start", "2024-01-01", "--end", "2025-02-01",
                    "--output", "local_data/verified_prices.csv",
                    "--report", "local_data/verified_coverage.json",
                ])
            # The CLI deliberately requires cache under local_data, so it
            # should reject external cache directories instead of downloading.
            self.assertEqual(status, 2)
            legal_cache = self.root / "local_data/us_cache"
            legal_cache.mkdir(parents=True, exist_ok=True)
            for p in self.cache.iterdir():
                (legal_cache / p.name).write_bytes(p.read_bytes())
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = main([
                    "stock-sync-market", "--universe", str(self.universe),
                    "--cache-dir", "local_data/us_cache",
                    "--start", "2024-01-01", "--end", "2025-02-01",
                    "--output", "local_data/verified_prices.csv",
                    "--report", "local_data/verified_coverage.json",
                ])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout.getvalue())["covered"], 6)
            self.assertTrue((self.root / "local_data/verified_prices.csv").exists())
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
