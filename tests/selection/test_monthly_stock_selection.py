"""Pure monthly stock selection calendar, readiness and non-execution tests."""

from __future__ import annotations

import contextlib
import csv
from datetime import date, datetime, timedelta
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from atlaspm.monthly_stock_selection import (
    SCHEMA, month_end_nyse_session, monthly_candidates, write_monthly_research,
)
from atlaspm.ai_stock_ranker_v2 import train_stock_learning_to_rank

try:
    import pandas_market_calendars
except ImportError:
    pandas_market_calendars = None
try:
    import lightgbm
except ImportError:
    lightgbm = None

HAS_FITTED_DEPS = pandas_market_calendars is not None and lightgbm is not None
NEW_YORK = ZoneInfo("America/New_York")


@unittest.skipUnless(pandas_market_calendars is not None, "Optional NYSE calendar not installed")
class MarketMonthEndDateTest(unittest.TestCase):
    def test_month_end_dates_are_actual_nyse_sessions(self):
        self.assertEqual(month_end_nyse_session("2026-11"), date(2026, 11, 30))
        self.assertEqual(month_end_nyse_session("2026-10"), date(2026, 10, 30))
        self.assertEqual(month_end_nyse_session("2023-12"), date(2023, 12, 29))
        # 2025 May 31 Saturday; May 30 Friday trading session
        self.assertEqual(month_end_nyse_session("2025-05"), date(2025, 5, 30))

    def test_month_validation(self):
        for invalid in ("2026-13", "2026-2", "Jan2026", "2026-00", "1999-12"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "month must be YYYY-MM"):
                    month_end_nyse_session(invalid)


@unittest.skipUnless(HAS_FITTED_DEPS, "Optional LightGBM + NYSE calendar dependencies not installed")
class MonthlyStockScreenerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prices = self.root / "prices.csv"
        self.benchmark = self.root / "benchmark.csv"
        self.model = self.root / "model.txt"
        self.days = []
        day = date(2020, 1, 1)
        while day < date(2024, 1, 1):
            if day.weekday() < 5:
                self.days.append(day)
            day += timedelta(days=1)
        with self.prices.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "symbol", "adjusted_close"])
            for i, d in enumerate(self.days):
                for j, name in enumerate("ABCDEF"):
                    drift = 0.00015 + 0.00008 * j
                    cyclic = ((i * (j + 3)) % 43) / 1700
                    w.writerow([d.isoformat(), name, 100 * (1 + drift)**i * (1 + cyclic)])
        with self.benchmark.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "symbol", "adjusted_close"])
            for i, d in enumerate(self.days):
                w.writerow([d.isoformat(), "SPY", 250 * 1.00031**i])
        train_stock_learning_to_rank(
            self.prices, self.benchmark, self.model,
            start="2020-01-01", train_end="2021-12-31",
            validation_end="2022-12-30", test_end="2023-12-29",
            num_boost_round=50, top=3, min_train_rows=30,
        )

    def _run(self, **kwargs):
        return monthly_candidates(
            self.prices, self.benchmark, self.model,
            month="2023-12", min_scored=5,
            as_at=datetime(2024, 1, 1, 17, 30, tzinfo=NEW_YORK),
            **kwargs,
        )

    def test_monthly_report_is_stock_only_and_reports_limited_universe(self):
        report = self._run(max_training_age_days=5000)
        self.assertEqual(report["schema"], SCHEMA)
        self.assertEqual(report["as_of_nyse_session"], "2023-12-29")
        self.assertEqual(report["next_calendar_month"], "2024-01")
        self.assertEqual(report["top_20"]["returned"], 6)
        self.assertEqual(report["top_50"]["returned"], 6)
        self.assertEqual(report["report_status"], "REVIEW_REQUIRED")
        self.assertEqual(report["data_quality"]["provided_price_symbols"], 6)
        self.assertEqual(report["data_quality"]["scored_at_month_end"], 6)
        self.assertIsNone(report["data_quality"]["listing_manifest_count"])
        self.assertTrue(any("TOP50_INCOMPLETE" in s for s in report["review_reasons"]))
        self.assertTrue(any("UNVERIFIED_FULL_US_MARKET_SCOPE" in s
                            for s in report["review_reasons"]))
        self.assertFalse(report["live_execution_enabled"])
        self.assertFalse(report["automated_handoff_approved"])
        self.assertTrue(report["research_only"])
        self.assertEqual(report["order_instructions"], [])
        self.assertEqual(len(report["model"]["sha256"]), 64)

    def test_cannot_publish_before_last_nyse_close(self):
        with self.assertRaisesRegex(ValueError, "is not complete"):
            monthly_candidates(
                self.prices, self.benchmark, self.model,
                month="2023-12", min_scored=5,
                as_at=datetime(2023, 12, 29, 16, 30, tzinfo=NEW_YORK),
            )
        with self.assertRaisesRegex(ValueError, "is not complete"):
            monthly_candidates(
                self.prices, self.benchmark, self.model,
                month="2026-11", min_scored=5,
                as_at=datetime(2026, 11, 22, 20, tzinfo=NEW_YORK),
            )

    def test_monthly_report_detects_stale_trained_model(self):
        report = self._run(max_training_age_days=90)
        self.assertGreater(report["model"]["training_age_days"], 90)
        self.assertTrue(any("STALE_MODEL" in reason for reason in report["review_reasons"]))

    def test_market_manifest_coverage_is_audited(self):
        manifest = self.root / "manifest.csv"
        with manifest.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["listing_symbol", "provider_symbol"])
            for symbol in "ABCDEF":
                writer.writerow([symbol, symbol])
            for i in range(20):
                writer.writerow([f"ZZ{i}", f"ZZ{i}"])
        result = self._run(universe_path=manifest, max_training_age_days=5000)
        self.assertEqual(result["data_quality"]["listing_manifest_count"], 26)
        self.assertAlmostEqual(result["data_quality"]["listed_market_coverage_pct"], 23.08)
        self.assertTrue(any("INSUFFICIENT_MARKET_COVERAGE" in x
                            for x in result["review_reasons"]))

    def test_missing_last_benchmark_close_must_fail(self):
        # No invention of month-end SPY close from prior market session.
        with self.benchmark.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        with self.benchmark.open("w", encoding="utf-8") as f:
            f.writelines(line for line in lines if "2023-12-29" not in line)
        with self.assertRaisesRegex(ValueError, "completed benchmark trading close"):
            self._run(max_training_age_days=5000)

    def test_dated_files_and_no_silent_overwrite(self):
        result = self._run()
        target = self.root / "reports"
        paths = write_monthly_research(result, target)
        report = json.loads(Path(paths["json_report"]).read_text(encoding="utf-8"))
        self.assertEqual(report["as_of_nyse_session"], "2023-12-29")
        self.assertEqual(report["report_status"], "REVIEW_REQUIRED")
        with Path(paths["csv_rankings"]).open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["month"], "2023-12")
        self.assertEqual(rows[0]["research_only"], "True")
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            write_monthly_research(result, target)
        self.assertEqual(write_monthly_research(result, target, overwrite=True), paths)

    def test_cli_private_paths_and_research_output(self):
        from atlaspm.cli import main
        old = Path.cwd()
        try:
            os.chdir(self.root)
            dest = Path("local_data/monthly")
            private_model = Path("local_data/model.txt")
            private_model.parent.mkdir(parents=True, exist_ok=True)
            private_model.write_bytes(self.model.read_bytes())
            private_model.with_suffix(".txt.meta.json").write_bytes(
                self.model.with_suffix(".txt.meta.json").read_bytes()
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                err = main([
                    "stock-ai-monthly",
                    "--prices", str(self.prices),
                    "--benchmark-prices", str(self.benchmark),
                    "--model", str(private_model),
                    "--month", "2023-12",
                    "--output-dir", "public_reports",
                ])
            self.assertEqual(err, 2)
            self.assertFalse((self.root / "public_reports").exists())

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                ok = main([
                    "stock-ai-monthly",
                    "--prices", str(self.prices),
                    "--benchmark-prices", str(self.benchmark),
                    "--model", str(private_model),
                    "--month", "2023-12",
                    "--min-scored", "5",
                    "--output-dir", str(dest),
                ])
            self.assertEqual(ok, 0)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["report_status"], "REVIEW_REQUIRED")
            self.assertFalse(summary["live_execution_enabled"])
            self.assertTrue((self.root / dest / "2023-12_monthly_stock_research.json").exists())
        finally:
            os.chdir(old)


if __name__ == "__main__":
    unittest.main()
