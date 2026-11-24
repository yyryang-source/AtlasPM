"""End-to-end checks for the matched selection benchmark."""

import contextlib
import csv
from datetime import date, timedelta
import importlib.util
import io
import json
from math import exp, sin
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atlaspm.cli import main
from atlaspm.research.stock_comparison import _select_unambiguous_top, compare_stock_selection


class StockComparisonRankingTest(unittest.TestCase):
    def test_a_tied_group_straddling_the_cutoff_is_discarded(self):
        rows = [{"symbol": symbol} for symbol in ("A", "B", "C")]
        self.assertEqual(_select_unambiguous_top(rows, [1.0, .5, .5], 2), (["A"], True))
        self.assertEqual(_select_unambiguous_top(rows, [1.0, 1.0, 1.0], 2), ([], True))
        self.assertEqual(_select_unambiguous_top(rows, [1.0, 1.0, .5], 2), (["A", "B"], False))


@unittest.skipUnless(importlib.util.find_spec("lightgbm"), "Optional LightGBM dependency")
class StockComparisonTest(unittest.TestCase):
    def test_matched_cohort_and_frozen_training_at_cli_entry_point(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            days = [date(2022, 1, 3) + timedelta(days=offset) for offset in range(720)]
            days = [day for day in days if day.weekday() < 5]
            symbols = ["A", "B", "C", "D"]
            prices = folder / "prices.csv"
            benchmark = folder / "benchmark.csv"
            universe = folder / "universe.csv"
            fundamentals = folder / "fundamentals.csv"
            with benchmark.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["date", "symbol", "adjusted_close"])
                writer.writerows((day, "SPY", 200 * exp(.0004 * i)) for i, day in enumerate(days))
            with universe.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["as_of_date", "symbol"])
                for i in range(250, len(days), 14):
                    writer.writerows((days[i], symbol) for symbol in symbols if i < 418 or symbol != "D")
            with fundamentals.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["as_of_date", "symbol", "sector", "market_cap_usd", "roe",
                                 "revenue_growth_yoy", "debt_to_equity", "fcf_yield", "ev_to_ebitda"])
                for i in range(230, len(days), 35):
                    for number, symbol in enumerate(symbols):
                        writer.writerow([days[i], symbol, "Example", 2e9, .15 + .03 * number,
                                         .11 + .04 * sin(i + number), .1 + .1 * number, .02, 12 + number])

            def write_prices(change_future=False):
                with prices.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["date", "symbol", "adjusted_close"])
                    for i, day in enumerate(days):
                        for n, symbol in enumerate(symbols):
                            if symbol == "C" and i == 259:
                                continue  # Missing label on a non-decision session.
                            close = (100 + n * 10) * exp(.0003 * i + .16 * sin(i / (11 + n) + n))
                            if change_future and symbol == "A" and i == 475:
                                close *= 1.1
                            writer.writerow([day, symbol, close])

            write_prices()
            options = {
                "train_start": str(days[253]), "train_end": str(days[350]),
                "validation_end": str(days[415]), "test_start": str(days[420]), "test_end": str(days[485]),
                "rebalance_days": 5, "top": 2, "num_boost_round": 12, "min_train_rows": 30,
            }
            args = ["stock-compare", "--prices", str(prices), "--benchmark-prices", str(benchmark),
                    "--universe-history", str(universe), "--fundamentals", str(fundamentals)]
            for key, value in options.items():
                args.extend(["--" + key.replace("_", "-"), str(value)])
            cwd = Path.cwd()
            try:
                os.chdir(folder)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(main(args), 0)
                summary = json.loads(out.getvalue())
                report = json.loads(Path(summary["written"]).read_text(encoding="utf-8"))
            finally:
                os.chdir(cwd)

            names = {"equal_weight", "technical", "fundamental", "lightgbm"}
            self.assertEqual(set(report["strategies"]), names)
            self.assertLessEqual(report["model"]["last_label_exit"]["train"], options["train_end"])
            self.assertLessEqual(report["model"]["last_label_exit"]["validation"], options["validation_end"])
            self.assertGreater(report["model"]["purged_boundary_cohorts"], 0)
            self.assertEqual(report["model"]["incomplete_cohorts"]["train"], 2)
            for decision, execution in zip(report["decisions"], report["executions"], strict=True):
                self.assertEqual(decision["execution_date"], execution["date"])
                self.assertEqual(set(execution["strategies"]), names)
                common = set(decision["selected"]["equal_weight"])
                self.assertEqual(common, {"A", "B", "C"})
                for selected in decision["selected"].values():
                    self.assertTrue(set(selected) <= common)
            self.assertGreater(report["strategies"]["equal_weight"]["total_cost_index"], 0)

            write_prices(change_future=True)
            rerun = compare_stock_selection(prices, benchmark, universe, fundamentals, **options)
            self.assertNotEqual(report["inputs_sha256"]["prices"], rerun["inputs_sha256"]["prices"])
            self.assertEqual(report["model"]["model_sha256"], rerun["model"]["model_sha256"])
            self.assertEqual(report["decisions"][0], rerun["decisions"][0])
