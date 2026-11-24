"""Contract, chronology and safety tests for offline stock selection."""

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.strategies.equity.stock_screener import screen_stocks


class StockScreenerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prices = self.root / "prices.csv"
        self.fundamentals = self.root / "fundamentals.csv"
        self.days = []
        d = date(2025, 1, 1)
        while len(self.days) < 270:
            if d.weekday() < 5:
                self.days.append(d)
            d += timedelta(days=1)
        self.as_of = self.days[-1].isoformat()
        self._write_prices()
        self._write_fundamentals()

    def _write_prices(self, future=False):
        with self.prices.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "symbol", "adjusted_close"])
            for i, day in enumerate(self.days):
                w.writerow([day.isoformat(), "AAA", 100 + i * .38 + (i % 4) * .1])
                w.writerow([day.isoformat(), "BBB", 120 - i * .11 + (i % 7) * .30])
                w.writerow([day.isoformat(), "CCC", 85 + i * .11 + (i % 4) * .3])
            if future:
                w.writerow([(self.days[-1] + timedelta(days=1)).isoformat(), "AAA", 9999999])

    def _write_fundamentals(self, future=False):
        with self.fundamentals.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["as_of_date", "symbol", "sector", "market_cap_usd", "roe",
                        "revenue_growth_yoy", "debt_to_equity", "fcf_yield", "ev_to_ebitda"])
            as_of = (self.days[-1] - timedelta(days=30)).isoformat()
            w.writerow([as_of, "AAA", "Technology", 2e10, .25, .18, .3, .06, 18])
            w.writerow([as_of, "BBB", "Energy", 3e10, .05, -.08, 2.5, -.02, 45])
            w.writerow([as_of, "CCC", "Technology", 4e10, .18, .11, .5, .03, 22])
            if future:
                w.writerow([(self.days[-1] + timedelta(days=1)).isoformat(), "BBB", "Energy", 3e10,
                            .9, 1, 0, .9, 1])

    def test_fundamental_and_technical_ranking(self):
        r = screen_stocks(self.prices, self.as_of, fundamentals_path=self.fundamentals)
        self.assertEqual(r["eligible_count"], 3)
        self.assertEqual(r["selected"][0]["symbol"], "AAA")
        self.assertEqual(r["mode"], "fundamental_plus_technical")
        self.assertEqual(sum(r["weights"].values()), 1.0)
        self.assertFalse(r["live_execution_enabled"])

    def test_price_only_and_sector_cap(self):
        r = screen_stocks(self.prices, self.as_of)
        self.assertEqual(r["mode"], "technical_only")
        self.assertEqual(len(r["selected"]), 3)
        s = screen_stocks(self.prices, self.as_of, fundamentals_path=self.fundamentals,
                          max_per_sector=1, top=3)
        self.assertEqual(len(s["selected"]), 2)
        self.assertEqual(len({row["sector"] for row in s["selected"]}), 2)
        with self.assertRaisesRegex(ValueError, "require --fundamentals"):
            screen_stocks(self.prices, self.as_of, max_per_sector=1)

    def test_cutoff_ignores_future_prices_and_fundamentals(self):
        base = screen_stocks(self.prices, self.as_of, fundamentals_path=self.fundamentals)
        self._write_prices(future=True)
        self._write_fundamentals(future=True)
        after = screen_stocks(self.prices, self.as_of, fundamentals_path=self.fundamentals)
        self.assertEqual(base["ranked"], after["ranked"])
        self.assertEqual(base["selected"], after["selected"])

    def test_future_fundamentals_do_not_qualify(self):
        with self.fundamentals.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["as_of_date", "symbol", "sector", "market_cap_usd", "roe", "revenue_growth_yoy",
                        "debt_to_equity", "fcf_yield", "ev_to_ebitda"])
            w.writerow([(self.days[-1] + timedelta(days=1)).isoformat(), "AAA", "Technology", 2e10,
                        .25, .18, .3, .06, 18])
        r = screen_stocks(self.prices, self.as_of, fundamentals_path=self.fundamentals)
        self.assertEqual(r["eligible_count"], 0)
        self.assertTrue(all(x["reason"] == "FUNDAMENTALS_NOT_AVAILABLE_AS_OF_DATE" for x in r["excluded"]))

    def test_staleness_and_risk_filters(self):
        r = screen_stocks(self.prices, (self.days[-1] + timedelta(days=25)).isoformat())
        self.assertEqual(r["eligible_count"], 0)
        self.assertTrue(all(row["reason"] == "STALE_PRICE" for row in r["excluded"]))
        v = screen_stocks(self.prices, self.as_of, max_annual_volatility=.00001)
        self.assertEqual(v["eligible_count"], 0)
        self.assertTrue(all(row["reason"] == "VOLATILITY_LIMIT" for row in v["excluded"]))

    def test_reject_invalid_publication_and_duplicate(self):
        with self.fundamentals.open("a", encoding="utf-8") as f:
            f.write(f"{self.days[-1].isoformat()},AAA,Technology,100000000,.2,.1,.1,.1,10\n")
            f.write(f"{self.days[-1].isoformat()},AAA,Technology,100000000,.2,.1,.1,.1,10\n")
        with self.assertRaisesRegex(ValueError, "Duplicate fundamentals"):
            screen_stocks(self.prices, self.as_of, fundamentals_path=self.fundamentals)

    def test_cli_private_json_output(self):
        from atlaspm.cli import main
        current = Path.cwd()
        try:
            os.chdir(self.root)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main([
                    "stock-screen", "--prices", str(self.prices),
                    "--as-of", self.as_of, "--top", "2",
                    "--output", "local_data/ranked.json",
                ])
            self.assertEqual(code, 0)
            summary = json.loads(out.getvalue())
            saved = json.loads((self.root / "local_data/ranked.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["selected"][0]["symbol"], saved["selected"][0]["symbol"])
            self.assertEqual(len(saved["selected"]), 2)
            self.assertFalse(saved["live_execution_enabled"])
        finally:
            os.chdir(current)

    def test_cli_requires_exact_decision_close_when_requested(self):
        from atlaspm.cli import main
        current = Path.cwd()
        try:
            os.chdir(self.root)
            next_day = (self.days[-1] + timedelta(days=1)).isoformat()
            with contextlib.redirect_stdout(io.StringIO()):
                code = main([
                    "stock-screen", "--prices", str(self.prices), "--as-of", next_day,
                    "--require-decision-close", "--output", "local_data/strict.json",
                ])
            self.assertEqual(code, 0)
            saved = json.loads((self.root / "local_data/strict.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["selected"], [])
            self.assertTrue(saved["filters"]["require_decision_close"])
            self.assertEqual({row["reason"] for row in saved["excluded"]}, {"MISSING_DECISION_CLOSE"})
            self.assertEqual(screen_stocks(self.prices, next_day)["eligible_count"], 3)
        finally:
            os.chdir(current)

    def test_cli_ranks_only_members_of_the_latest_available_snapshot(self):
        from atlaspm.cli import main
        universe = self.root / "universe.csv"
        with universe.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerows([
                ["as_of_date", "symbol"],
                [self.days[-1] + timedelta(days=1), "AAA"],
                [self.days[-2], "CCC"],
                [self.days[-3], "BBB"],
            ])
        current = Path.cwd()
        try:
            os.chdir(self.root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "stock-screen", "--prices", str(self.prices), "--as-of", self.as_of,
                    "--universe-history", str(universe), "--max-universe-age-days", "7",
                    "--output", "local_data/member_rank.json",
                ])
            self.assertEqual(code, 0)
            summary = json.loads(output.getvalue())
            self.assertEqual(summary["universe_source"], "historical_snapshots")
            self.assertEqual(summary["universe_snapshot_as_of"], self.days[-2].isoformat())
            self.assertEqual([row["symbol"] for row in summary["selected"]], ["CCC"])
        finally:
            os.chdir(current)

    def test_cli_blocks_public_repository_output(self):
        from atlaspm.cli import main
        current = Path.cwd()
        try:
            os.chdir(self.root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main([
                    "stock-screen", "--prices", str(self.prices),
                    "--as-of", self.as_of, "--output", "not-private.json",
                ])
            self.assertEqual(code, 2)
            self.assertIn("AtlasPM error:", err.getvalue())
            self.assertFalse((self.root / "not-private.json").exists())
        finally:
            os.chdir(current)


if __name__ == "__main__":
    unittest.main()
