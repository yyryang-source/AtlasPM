"""AtlasPM AI stock screener chronology, ranking, metadata and CLI tests."""

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

from atlaspm.ai_stock_ranker import (
    FEATURE_NAMES, _features, _spearman, _rank_predictions_with_ties, chronological_splits,
    create_training_rows, evaluate_ranker, rank_with_ai, train_ai_stock_ranker,
)

try:
    import lightgbm
except ImportError:
    lightgbm = None


class AIStockRankerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prices = self.root / "prices.csv"
        self.benchmark = self.root / "spy.csv"
        self.model = self.root / "model.txt"
        self.dates: list[date] = []
        day = date(2020, 1, 1)
        while day < date(2024, 1, 1):
            if day.weekday() < 5:
                self.dates.append(day)
            day += timedelta(days=1)
        self.cutoff = self.dates[-5].isoformat()
        self._prices()

    def _prices(self):
        with self.prices.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "symbol", "adjusted_close"])
            for i, d in enumerate(self.dates):
                for j, symbol in enumerate("ABCDEF"):
                    drift = 0.00025 + j * 0.00008
                    # Diversified deterministic sequences for all symbols.
                    factor = (1.0 + drift) ** i
                    modulation = 1.0 + ((i * (j + 3)) % 31) / 1500
                    w.writerow([d.isoformat(), symbol, 75 * factor * modulation])
        with self.benchmark.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "symbol", "adjusted_close"])
            for i, d in enumerate(self.dates):
                w.writerow([d.isoformat(), "SPY", 100 * (1.00030 ** i) * (1 + (i % 13)/2000)])

    def _dataset(self):
        return create_training_rows(
            self.prices, self.benchmark,
            start="2020-01-01", test_end="2023-12-29",
            sample_every_sessions=21, horizon_sessions=21,
        )

    def test_features_use_no_future_prices(self):
        days = self.dates[:270]
        prices = [100 * 1.001 ** i for i in range(len(days))]
        normal = _features((days, prices), days[252])
        future = _features((days, prices[:-1] + [1e15]), days[252])
        self.assertEqual(normal, future)
        self.assertEqual(len(normal), len(FEATURE_NAMES))
        self.assertIsNone(_features((days, prices), days[251]))

    def test_chronological_and_label_purge(self):
        rows, meta = self._dataset()
        self.assertGreater(len(rows), 100)
        self.assertEqual(meta["feature_names"], list(FEATURE_NAMES))
        self.assertEqual(meta["source_stock_universe"], 6)
        splits = chronological_splits(
            rows, train_end="2021-12-31",
            validation_end="2022-12-30", test_end="2023-12-29",
        )
        self.assertGreater(len(splits["train"]), 30)
        self.assertGreater(len(splits["validation"]), 30)
        self.assertGreater(len(splits["test"]), 30)
        for name, top in [("train", "2021-12-31"),
                          ("validation", "2022-12-30"), ("test", "2023-12-29")]:
            self.assertTrue(all(r["label_exit_date"] <= top for r in splits[name]))
        self.assertTrue(all(r["date"] > "2021-12-31" for r in splits["validation"]))
        self.assertTrue(all(r["date"] > "2022-12-30" for r in splits["test"]))

    def test_future_prices_dont_change_earlier_data(self):
        before, _ = self._dataset()
        future = date(2024, 3, 1).isoformat()
        with self.prices.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for symbol in "ABCDEF":
                w.writerow([future, symbol, 1000000])
        with self.benchmark.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([future, "SPY", 2000000])
        after, _ = self._dataset()
        self.assertEqual(before, after)

    def test_strict_next_close_labels(self):
        rows, _ = self._dataset()
        first = next(r for r in rows if r["symbol"] == "A")
        self.assertEqual(first["date"], self.dates[252].isoformat())
        self.assertEqual(first["entry_date"], self.dates[253].isoformat())
        self.assertEqual(first["label_exit_date"], self.dates[274].isoformat())

    def test_rank_metrics_and_validation(self):
        rows = [
            {"date": "2021-01-01", "symbol": name, "features": [float(i)] * 9,
             "forward_excess_return": float(i) / 10}
            for i, name in enumerate("ABCDE")
        ]
        r = evaluate_ranker(rows, [0., 1., 2., 3., 4.], top=2)
        self.assertAlmostEqual(r["mean_spearman_ic"], 1.0)
        self.assertAlmostEqual(r["simple_momentum_mean_spearman_ic"], 1.0)
        self.assertGreater(r["mean_top_n_minus_same_universe_mean"], 0)
        self.assertAlmostEqual(_spearman([1., 2., 3.], [3., 2., 1.]), -1.0)
        with self.assertRaisesRegex(ValueError, "Prediction length mismatch"):
            evaluate_ranker(rows, [])

    def test_tie_rank_is_not_fabricated_alphabetical_preference(self):
        rows = [{"symbol": s} for s in "ABCDE"]
        preds = [0.02, 0.01, 0.005, 0.005, 0.005]
        ranked, d = _rank_predictions_with_ties(rows, preds, top=3)
        self.assertEqual([r["rank"] for r in ranked], [1, 2, 3, 3, 3])
        self.assertEqual([r["display_position"] for r in ranked], [1, 2, 3, 4, 5])
        self.assertEqual(d["distinct_displayed_score_levels"], 3)
        self.assertEqual(d["largest_tied_group"], 3)
        self.assertEqual(d["stocks_with_tied_scores"], 3)
        self.assertTrue(d["ties_cross_top_n_cutoff"])
        self.assertFalse(d["top_n_is_unambiguous"])
        self.assertEqual(d["rank_quality"], "REVIEW_REQUIRED")
        self.assertTrue(all(r["tie_count"] == 3 for r in ranked[2:]))
        _, all_names = _rank_predictions_with_ties(rows, preds, top=5)
        self.assertFalse(all_names["ties_cross_top_n_cutoff"])

    def test_visible_precision_ties_are_communicated(self):
        rows = [{"symbol": s} for s in "ABC"]
        # Not exactly the same float, but identical to displayed 4 decimals %.
        ranked, d = _rank_predictions_with_ties(
            rows, [0.01, 0.01000000003, 0.005], top=1
        )
        self.assertEqual(ranked[0]["rank"], ranked[1]["rank"])
        self.assertTrue(d["ties_cross_top_n_cutoff"])
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            _rank_predictions_with_ties(rows, [0.1, float("nan"), 0.03], top=2)

    def test_invalid_splits_rejected(self):
        rows, _ = self._dataset()
        with self.assertRaisesRegex(ValueError, "train-end < validation-end"):
            chronological_splits(
                rows, train_end="2022-12-30",
                validation_end="2021-12-31", test_end="2023-12-29",
            )
        with self.assertRaisesRegex(ValueError, "earlier than test end"):
            create_training_rows(
                self.prices, self.benchmark,
                start="2024-01-01", test_end="2023-12-29",
            )

    def test_cli_blocks_model_written_outside_private_dir(self):
        from atlaspm.cli import main
        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                status = main([
                    "stock-ai-train",
                    "--prices", str(self.prices),
                    "--benchmark-prices", str(self.benchmark),
                    "--model-output", "public_model.txt",
                ])
            self.assertEqual(status, 2)
            self.assertFalse((self.root / "public_model.txt").exists())
        finally:
            os.chdir(cwd)

    @unittest.skipIf(lightgbm is None, "Optional LightGBM dependency not installed")
    def test_real_lightgbm_train_test_and_infer(self):
        result = train_ai_stock_ranker(
            self.prices, self.benchmark, self.model,
            start="2020-01-01",
            train_end="2021-12-31",
            validation_end="2022-12-30",
            test_end="2023-12-29",
            sample_every_sessions=21,
            horizon_sessions=21, min_train_rows=30,
            num_boost_round=40, top=3,
        )
        self.assertGreater(result["split_counts"]["train"], 30)
        self.assertGreater(result["split_counts"]["validation"], 30)
        self.assertGreater(result["split_counts"]["test"], 30)
        self.assertGreaterEqual(result["purged_rows"], 0)
        self.assertIn("mean_spearman_ic", result["test"])
        self.assertFalse(result["live_execution_enabled"])
        self.assertTrue(self.model.exists())
        self.assertTrue(self.model.with_suffix(".txt.meta.json").exists())
        scored = rank_with_ai(
            self.prices, self.benchmark, self.model,
            as_of=self.cutoff, top=4,
        )
        self.assertEqual(scored["scored_count"], 6)
        self.assertEqual(len(scored["selected"]), 4)
        self.assertEqual(scored["selected"][0]["rank"], 1)
        self.assertFalse(scored["live_execution_enabled"])
        self.assertEqual(scored["ranking_diagnostics"]["scored_stocks"], 6)
        self.assertEqual(scored["ranking_diagnostics"]["requested_top_n"], 4)
        self.assertIn("rank_quality", scored["ranking_diagnostics"])
        self.assertIn("number_of_trees_saved", scored["model_diagnostics"])
        self.assertEqual(len(scored["model_diagnostics"]["feature_importance"]),
                         len(FEATURE_NAMES))
        with self.assertRaisesRegex(ValueError, "knowledge cutoff"):
            rank_with_ai(
                self.prices, self.benchmark, self.model,
                as_of="2021-12-31",
            )
        with self.assertRaisesRegex(ValueError, "differs from training benchmark"):
            rank_with_ai(
                self.prices, self.benchmark, self.model,
                as_of=self.cutoff, benchmark_symbol="QQQ",
            )


if __name__ == "__main__":
    unittest.main()
