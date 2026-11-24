"""Offline safeguards for interrupted thousands-of-stocks Yahoo quote sync."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from atlaspm.data.us_market_prices import sync_us_market_prices

START = "2024-01-01"
END = "2024-02-01"


class BoundedStockMarketSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / "listed.csv"
        self.cache = self.root / "local_data" / "quote_cache"
        self.output = self.root / "local_data" / "whole_market.csv"
        self.names = ["AAPL", "AMD", "GOOG", "JPM", "NVDA", "WMT"]
        with self.manifest.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["listing_symbol", "provider_symbol", "security_name",
                        "exchange", "classification", "listing_source", "snapshot_utc"])
            for symbol in self.names:
                w.writerow([symbol, symbol, f"{symbol} Common Stock", "NASDAQ",
                            "name_confirmed_equity", "nasdaq",
                            "2026-11-22T00:00:00+00:00"])

    @staticmethod
    def fake_downloader(symbol, start, end):
        if start != START or end != END:
            raise AssertionError("Date bounds changed, cache would be invalidated")
        return [("2024-01-02", 110.0), ("2024-01-03", 111.0)]

    def run_sync(self, **kwargs):
        config = {
            "start": START, "end": END, "attempts": 1, "workers": 1,
            "batch_size": 2, "batch_pause_seconds": 0.0,
            "max_consecutive_failures": 12, "max_rate_limit_errors": 2,
            "downloader": self.fake_downloader,
        }
        config.update(kwargs)
        return sync_us_market_prices(
            self.manifest, self.cache, self.output, **config,
        )

    def test_batch_cap_retains_all_cache_success_and_does_not_redownload(self):
        tracker = []
        def tracked(symbol, start, end):
            tracker.append(symbol)
            return self.fake_downloader(symbol, start, end)
        result = self.run_sync(downloader=tracked, max_symbols=2)
        self.assertEqual(result["new_download_successes"], 2)
        self.assertEqual(result["attempted_downloads"], 2)
        self.assertEqual(result["cached_or_downloaded_symbols"], 2)
        self.assertFalse(result["stopped_early"])
        self.assertEqual(len(tracker), 2)
        with self.output.open(newline="") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 4)
        more = self.run_sync(downloader=tracked, max_symbols=2)
        self.assertEqual(more["cached_or_downloaded_symbols"], 4)
        self.assertEqual(more["new_download_successes"], 2)
        self.assertEqual(len(tracker), 4)

    def test_cache_only_recovers_interrupted_local_cache_without_network(self):
        self.run_sync(max_symbols=3)
        def forbidden(*_args):
            raise AssertionError("cache-only attempted network download")
        result = self.run_sync(cache_only=True, downloader=forbidden)
        self.assertEqual(result["download_status"], "CACHE_ONLY")
        self.assertFalse(result["stopped_early"])
        self.assertIsNone(result["stop_reason"])
        self.assertEqual(result["cached_or_downloaded_symbols"], 3)
        self.assertEqual(result["price_rows"], 6)
        self.assertEqual(result["attempted_downloads"], 0)
        self.assertEqual(result["requested_downloads"], 0)
        self.assertEqual(result["not_attempted_due_to_stop"], 0)
        self.assertEqual(result["missing_symbols"], 3)

    def test_rate_limit_circuit_breaker_prevents_thousands_of_extra_calls(self):
        counter = []
        def limited(symbol, start, end):
            counter.append(symbol)
            if symbol == "AAPL":
                raise RuntimeError("YFRateLimitError: Too Many Requests HTTP 429")
            return self.fake_downloader(symbol, start, end)
        result = self.run_sync(
            downloader=limited, attempts=3, max_rate_limit_errors=1,
        )
        self.assertEqual(result["stop_reason"], "PROVIDER_RATE_LIMIT")
        self.assertTrue(result["stopped_early"])
        self.assertEqual(result["download_status"], "STOPPED_EARLY")
        self.assertEqual(result["attempted_downloads"], 2)
        self.assertEqual(result["not_attempted_due_to_stop"], 4)
        self.assertEqual(result["provider_rate_limit_errors"], 1)
        self.assertEqual(counter.count("AAPL"), 1, "429 must not be immediately retried")
        self.assertEqual(result["cached_or_downloaded_symbols"], 1)
        self.assertEqual(result["missing_symbols"], 5)
        self.assertTrue(self.output.exists())

    def test_sustained_failures_stop_after_first_batch(self):
        counter = []
        def missing(symbol, start, end):
            counter.append(symbol)
            raise ValueError("NO_HISTORY_RETURNED")
        result = self.run_sync(
            downloader=missing, max_consecutive_failures=2,
        )
        self.assertEqual(result["stop_reason"], "CONSECUTIVE_DOWNLOAD_FAILURES")
        self.assertEqual(result["attempted_downloads"], 2)
        self.assertEqual(result["not_attempted_due_to_stop"], 4)
        self.assertEqual(result["missing_symbols"], 6)
        self.assertEqual(len(counter), 2)
        with self.output.open(newline="") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 0)

    def test_ctrl_c_between_batches_still_builds_partial_csv_and_report(self):
        # Interruption while paused between bounded batches is recoverable.
        with mock.patch("atlaspm.data.us_market_prices.time.sleep",
                        side_effect=KeyboardInterrupt):
            result = self.run_sync(batch_pause_seconds=0.1)
        self.assertTrue(result["stopped_early"])
        self.assertEqual(result["stop_reason"], "USER_INTERRUPTED")
        self.assertEqual(result["cached_or_downloaded_symbols"], 2)
        self.assertEqual(result["attempted_downloads"], 2)
        self.assertEqual(result["not_attempted_due_to_stop"], 4)
        self.assertTrue(self.output.exists())
        # Rebuild an accurate cache-only snapshot after the partial stop.
        snap = self.run_sync(cache_only=True)
        self.assertEqual(snap["cached_or_downloaded_symbols"], 2)

    def test_parameter_validation(self):
        cases = [
            {"batch_size": 0},
            {"batch_size": 101},
            {"batch_pause_seconds": float("nan")},
            {"batch_pause_seconds": -1.0},
            {"max_consecutive_failures": 0},
            {"max_rate_limit_errors": 0},
            {"cache_only": True, "max_symbols": 2},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.run_sync(**overrides)
        self.assertFalse(self.output.exists())

    def test_cli_cache_only_writes_current_report_without_network(self):
        from atlaspm.cli import main
        self.run_sync(max_symbols=3)
        saved_cwd = Path.cwd()
        try:
            os.chdir(self.root)
            output = "local_data/from_cache_only.csv"
            report = "local_data/from_cache_only.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = main([
                    "stock-sync-market", "--universe", str(self.manifest),
                    "--start", START, "--end", END,
                    "--cache-dir", "local_data/quote_cache",
                    "--output", output, "--report", report,
                    "--cache-only", "--max-symbols", "0",
                ])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout.getvalue())["download_status"], "CACHE_ONLY")
            payload = json.loads((self.root / report).read_text(encoding="utf-8"))
            self.assertEqual(payload["attempted_downloads"], 0)
            self.assertEqual(payload["cached_or_downloaded_symbols"], 3)
            self.assertTrue((self.root / output).exists())
        finally:
            os.chdir(saved_cwd)


if __name__ == "__main__":
    unittest.main()
