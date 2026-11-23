"""Offline, no-network regression tests for 20-signal research screener."""

from __future__ import annotations

import csv
from datetime import date, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest

from atlaspm.selection.consensus_20_screener import (
    _percentiles, _signals, SIGNALS, SIGNAL_FAMILIES, FAMILY_WEIGHTS,
    run_20_signal_research,
)


def trading_days(count=280):
    result = []
    day = date(2025, 1, 1)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day)
        day += timedelta(days=1)
    return result


class ConsensusTwentyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prices = self.root / "prices.csv"
        self.dates = trading_days()
        self.as_of = self.dates[-1].isoformat()

    def write_prices(self, names=12, *, extra_future=False, split_name=None,
                     duplicated=False, identical=False):
        with self.prices.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["date", "symbol", "adjusted_close"])
            for j in range(names):
                symbol = split_name if j == names - 1 and split_name else f"T{j:02d}"
                for i, day in enumerate(self.dates):
                    if identical:
                        px = 20 * (1.0004 ** i)
                    else:
                        px = 20 * ((1 + 0.00025 + j * 0.00002) ** i)
                        px *= 1 + (((i + 3) * (j + 2)) % 19) / 3000
                    if symbol == split_name and i >= 150:
                        px *= 25
                    writer.writerow([day, symbol, f"{px:.8f}"])
                    if duplicated and j == 0 and i == 150:
                        writer.writerow([day, symbol, f"{px:.8f}"])
                if extra_future:
                    writer.writerow([self.dates[-1] + timedelta(days=7), symbol, "999999"])

    def test_20_distinct_nonempty_signals_in_five_families(self):
        self.assertEqual(len(SIGNALS), 20)
        self.assertEqual(len(set(SIGNALS)), 20)
        self.assertEqual(len(SIGNAL_FAMILIES), 5)
        self.assertAlmostEqual(sum(FAMILY_WEIGHTS.values()), 1.0)
        closes = [20 * 1.0005 ** i * (1 + (i % 11) / 1000)
                  for i in range(253)]
        signals = _signals(closes)
        self.assertEqual(set(signals), set(SIGNALS))
        self.assertLess(signals["low_volatility_63"], 0)
        self.assertGreater(signals["positive_days_63"], 0.5)

    def test_percentiles_are_tie_neutral(self):
        ranked = _percentiles({"C": 5.0, "A": 1.0, "B": 5.0, "D": 0.0})
        self.assertAlmostEqual(ranked["A"], 1 / 3)
        self.assertAlmostEqual(ranked["B"], 5 / 6)
        self.assertAlmostEqual(ranked["C"], 5 / 6)
        self.assertEqual(ranked["D"], 0)
        self.assertEqual(_percentiles({"Z": 1, "Y": 1}), {"Y": .5, "Z": .5})

    def test_full_pipeline_private_research_only_and_20_active_features(self):
        self.write_prices()
        r = run_20_signal_research(
            self.prices, as_of=self.as_of, top=3,
            min_price=1, min_screenable=3,
        )
        self.assertEqual(r["n_signals"], 20)
        self.assertEqual(r["n_families"], 5)
        self.assertEqual(r["data_quality"]["eligible_price_only_names"], 12)
        self.assertEqual(r["top_requested"], 3)
        self.assertEqual(r["status"], "REVIEW_REQUIRED")
        self.assertFalse(r["live_execution_enabled"])
        self.assertFalse(r["automated_handoff_approved"])
        self.assertFalse(r["alpha_validated"])
        self.assertTrue(r["research_only"])
        self.assertEqual(len(r["selected"]), 3)
        self.assertEqual(len(r["preview_not_trade_instructions"]), 12)
        self.assertTrue(all(0 <= row["consensus_score"] <= 100 for row in r["selected"]))
        self.assertTrue(all(len(row["family_percentile_scores"]) == 5 for row in r["selected"]))
        self.assertEqual(r["selected"], r["preview_not_trade_instructions"][:3])
        self.assertEqual(len({r["symbol"] for r in r["selected"]}), 3)
        self.assertIn("LIQUIDITY_UNVERIFIED", " ".join(r["review_reasons"]))

    def test_future_closes_cannot_change_historical_ranks(self):
        self.write_prices()
        before = run_20_signal_research(
            self.prices, as_of=self.as_of, top=3, min_price=1, min_screenable=3,
        )
        self.write_prices(extra_future=True)
        after = run_20_signal_research(
            self.prices, as_of=self.as_of, top=3, min_price=1, min_screenable=3,
        )
        self.assertEqual(after["selected"], before["selected"])
        self.assertEqual(after["data_quality"]["future_price_rows_ignored"], 12)

    def test_unadjusted_reverse_split_is_quarantined_not_repaired(self):
        self.write_prices(split_name="DUKR")
        r = run_20_signal_research(
            self.prices, as_of=self.as_of, top=3, min_price=1, min_screenable=3,
        )
        self.assertEqual(r["data_quality"]["eligible_price_only_names"], 11)
        self.assertEqual(
            r["data_quality"]["excluded_counts"]["SUSPECT_CORPORATE_ACTION_OR_EXTREME_JUMP"], 1,
        )
        self.assertEqual(r["data_quality"]["corporate_action_extreme_jump_samples"][0]["symbol"], "DUKR")
        self.assertNotIn("DUKR", [x["symbol"] for x in r["preview_not_trade_instructions"]])
        self.assertFalse(r["alpha_validated"])

    def test_no_arbitrary_ticker_broken_top_on_identical_price_paths(self):
        self.write_prices(identical=True)
        r = run_20_signal_research(
            self.prices, as_of=self.as_of, top=3, min_price=1, min_screenable=3,
        )
        self.assertFalse(r["top_n_unambiguous"])
        self.assertEqual(r["selected"], [])
        self.assertTrue(any("TOP_N_CUTOFF_TIED" in x for x in r["review_reasons"]))
        self.assertTrue(all(x["tie_count"] == 12 for x in r["preview_not_trade_instructions"]))

    def test_aborts_on_duplicate_or_out_of_order_same_symbol(self):
        self.write_prices(duplicated=True)
        with self.assertRaisesRegex(ValueError, "Duplicate or nonchronological"):
            run_20_signal_research(
                self.prices, as_of=self.as_of, min_price=1, min_screenable=3,
            )

    def test_invalid_criteria_rejected(self):
        self.write_prices()
        for kwargs in ({"top": 0}, {"min_price": -1},
                       {"max_daily_ratio": 1.0}, {"min_screenable": 2},
                       {"as_of": "not-a-date"}):
            with self.subTest(kwargs=kwargs):
                arguments = {"as_of": self.as_of, "min_price": 1, "min_screenable": 3}
                arguments.update(kwargs)
                with self.assertRaises(ValueError):
                    run_20_signal_research(self.prices, **arguments)

    def test_cli_refuses_public_output_and_never_reads_market(self):
        from atlaspm.cli import main
        old = Path.cwd()
        try:
            os.chdir(self.root)
            result = main([
                "stock-consensus-20", "--prices", "does-not-exist.csv",
                "--as-of", self.as_of, "--output", "public.json",
            ])
            self.assertEqual(result, 2)
            self.assertFalse((self.root / "public.json").exists())
        finally:
            os.chdir(old)


if __name__ == "__main__":
    unittest.main()
