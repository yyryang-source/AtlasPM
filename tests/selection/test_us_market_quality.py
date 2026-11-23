"""Synthetic offline US-stock exchange-calendar quote completeness / audit tests."""

from __future__ import annotations

import contextlib
import csv
from datetime import date
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from atlaspm.selection.us_market_quality import SCHEMA, audit_current_us_stock_prices

try:
    import pandas_market_calendars as mcal
except ImportError:
    mcal = None


@unittest.skipUnless(mcal is not None, "Optional official NYSE calendar is required")
class USMarketQualityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = self.root / "universe.csv"
        self.prices = self.root / "prices.csv"
        self.names = ["AAPL", "AMD", "GOOGL", "JPM", "NVDA", "WMT"]
        self.as_of = "2026-10-30"
        self.sessions = [
            x.date().isoformat() for x in mcal.get_calendar("NYSE").valid_days(
                start_date="2026-01-02", end_date=self.as_of
            )
        ]
        self.assertEqual(self.sessions[-1], self.as_of)
        with self.manifest.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "listing_symbol", "provider_symbol", "security_name", "exchange",
                "classification", "listing_source", "snapshot_utc",
            ])
            for s in self.names:
                w.writerow([
                    s, s, f"{s} Common Stock", "NASDAQ",
                    "name_confirmed_equity", "nasdaq",
                    "2026-10-30T15:00:00+00:00",
                ])
        self.write_prices()

    def write_prices(self, skip_last=(), short=(), duplicate=False,
                     unknown=False, future=False, invalid=False):
        with self.prices.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "symbol", "adjusted_close"])
            # Real source files can be DATE-major; enforce a monotonic
            # chronological progression for each ticker even when interleaved.
            for i, date_string in enumerate(self.sessions):
                for j, symbol in enumerate(self.names):
                    if symbol in skip_last and date_string == self.as_of:
                        continue
                    if symbol in short and i < 150:
                        continue
                    w.writerow([date_string, symbol, 100 + i * 0.2 + j])
            if duplicate:
                w.writerow([self.as_of, "AAPL", 123])
            if unknown:
                w.writerow([self.as_of, "UNKNOWN", 123])
            if future:
                w.writerow(["2026-11-02", "AAPL", 125])
            if invalid:
                w.writerow(["2026-11-02", "WMT", "-5"])

    def audit(self, **kwargs):
        defaults = {
            "universe_path": self.manifest,
            "prices_path": self.prices,
            "as_of": self.as_of,
            "min_history_sessions": 100,
            "recent_sessions": 21,
            "min_recent_coverage_pct": 100,
            "min_month_end_coverage_pct": 90,
            "min_eligible_names": 5,
        }
        defaults.update(kwargs)
        return audit_current_us_stock_prices(**defaults)

    def test_good_whole_market_fixture_is_ready_for_research_only(self):
        result = self.audit()
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["market_data_status"], "READY_FOR_RESEARCH_REVIEW")
        self.assertEqual(result["listed_stock_universe_count"], 6)
        self.assertEqual(result["stocks_with_exact_decision_close"], 6)
        self.assertEqual(result["eligible_history_and_freshness"], 6)
        self.assertEqual(result["decision_close_coverage_pct"], 100)
        self.assertEqual(result["review_reasons"], [])
        self.assertEqual(result["eligible_symbols"], self.names)
        self.assertFalse(result["historical_point_in_time_membership_verified"])
        self.assertFalse(result["automated_handoff_approved"])
        self.assertFalse(result["live_execution_enabled"])
        self.assertEqual(result["order_instructions"], [])

    def test_missing_month_end_price_and_short_history_are_not_eligible(self):
        self.write_prices(skip_last={"AMD"}, short={"GOOGL"})
        result = self.audit()
        self.assertEqual(result["market_data_status"], "REVIEW_REQUIRED")
        self.assertEqual(result["stocks_with_exact_decision_close"], 5)
        self.assertEqual(result["eligible_history_and_freshness"], 4)
        self.assertEqual(result["missing_last_close_count"], 1)
        self.assertIn("AMD", result["missing_last_close_examples"])
        self.assertEqual(result["insufficient_history_count"], 1)
        self.assertIn("GOOGL", result["insufficient_history_examples"])
        self.assertTrue(any("INSUFFICIENT_DECISION_CLOSE_COVERAGE" in x
                            for x in result["review_reasons"]))

    def test_high_month_end_coverage_does_not_hide_thin_trained_universe(self):
        # All six tickers have month-end closes, but only three tickers
        # have enough price history for ranker feature windows.
        self.write_prices(short={"AMD", "GOOGL", "JPM"})
        audit = audit_current_us_stock_prices(
            universe_path=self.manifest, prices_path=self.prices,
            as_of=self.as_of, min_history_sessions=100,
            recent_sessions=21, min_recent_coverage_pct=100,
            min_month_end_coverage_pct=90, min_eligible_coverage_pct=75,
            min_eligible_names=1,
        )
        self.assertEqual(audit["stocks_with_exact_decision_close"], 6)
        self.assertEqual(audit["decision_close_coverage_pct"], 100)
        self.assertEqual(audit["eligible_history_and_freshness"], 3)
        self.assertEqual(audit["eligible_coverage_pct"], 50)
        self.assertEqual(audit["market_data_status"], "REVIEW_REQUIRED")
        self.assertTrue(any("INSUFFICIENT_SCOREABLE_MARKET_COVERAGE" in x
                            for x in audit["review_reasons"]))

    def test_detect_duplicate_unknown_invalid_without_silent_acceptance(self):
        self.write_prices(duplicate=True, unknown=True, invalid=True)
        result = self.audit()
        self.assertEqual(result["duplicate_or_out_of_order_quote_rows"], 1)
        self.assertEqual(result["rows_outside_listing_manifest"], 1)
        self.assertEqual(result["invalid_quote_rows"], 1)
        self.assertEqual(result["market_data_status"], "REVIEW_REQUIRED")
        self.assertIn("UNKNOWN", result["out_of_manifest_symbols_sample"])

    def test_recent_weekend_quote_still_triggers_calendar_review(self):
        # A Saturday price in the trailing window must remain visible to QC.
        with self.prices.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        anchor = next(
            index for index, row in enumerate(rows)
            if row[:2] == ["2026-10-23", "AAPL"]
        )
        rows.insert(anchor + 1, ["2026-10-24", "AAPL", "125"])
        with self.prices.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)
        report = self.audit()
        self.assertEqual(report["off_nyse_calendar_recent_quote_rows"], 1)
        self.assertEqual(report["market_data_status"], "REVIEW_REQUIRED")
        self.assertEqual(report["duplicate_or_out_of_order_quote_rows"], 0)

    def test_future_records_never_qualify_as_decision_date_history(self):
        self.write_prices(skip_last={"AMD"}, future=True)
        result = self.audit()
        self.assertEqual(result["future_dated_quote_rows_ignored"], 1)
        self.assertEqual(result["stocks_with_exact_decision_close"], 5)
        self.assertNotIn("AMD", result["eligible_symbols"])
        self.assertNotIn("2026-11-02", result["as_of_nyse_session"])

    def test_snapshot_after_historical_decision_gets_review_flag(self):
        s = self.manifest.read_text(encoding="utf-8")
        self.manifest.write_text(
            s.replace("2026-10-30T15:00:00+00:00", "2026-11-22T15:00:00+00:00"),
            encoding="utf-8",
        )
        result = self.audit()
        self.assertEqual(result["market_data_status"], "REVIEW_REQUIRED")
        self.assertTrue(any("POSTDATED_LISTING_MANIFEST" in x
                            for x in result["review_reasons"]))

    def test_only_nyse_decision_session_allowed(self):
        with self.assertRaisesRegex(ValueError, "not an NYSE trading session"):
            audit_current_us_stock_prices(
                universe_path=self.manifest, prices_path=self.prices,
                as_of="2026-11-22",
            )

    def test_invalid_parameters_fail_closed(self):
        for kwargs in (
            {"min_history_sessions": 0},
            {"recent_sessions": 0},
            {"min_month_end_coverage_pct": float("nan")},
            {"min_eligible_coverage_pct": float("nan")},
            {"min_eligible_names": True},
            {"max_manifest_age_days": -1},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.audit(**kwargs)

    def test_cli_audit_private_reports_and_reject_public_path(self):
        from atlaspm.cli import main
        old = Path.cwd()
        try:
            os.chdir(self.root)
            private = self.root / "local_data" / "market_audit.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                ok = main([
                    "stock-market-audit",
                    "--universe", str(self.manifest),
                    "--prices", str(self.prices),
                    "--as-of", self.as_of,
                    "--min-history-sessions", "100",
                    "--recent-sessions", "21",
                    "--min-recent-coverage-pct", "100",
                    "--min-eligible-names", "5",
                    "--output", str(private),
                ])
            self.assertEqual(ok, 0)
            self.assertEqual(json.loads(stdout.getvalue())["market_data_status"],
                             "READY_FOR_RESEARCH_REVIEW")
            self.assertEqual(json.loads(private.read_text(encoding="utf-8"))[
                "eligible_history_and_freshness"
            ], 6)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rejected = main([
                    "stock-market-audit", "--universe", str(self.manifest),
                    "--prices", str(self.prices), "--as-of", self.as_of,
                    "--output", "public_audit.json",
                ])
            self.assertEqual(rejected, 2)
            self.assertFalse((self.root / "public_audit.json").exists())
        finally:
            os.chdir(old)


if __name__ == "__main__":
    unittest.main()
