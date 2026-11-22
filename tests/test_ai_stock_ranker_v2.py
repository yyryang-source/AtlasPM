"""Learning-to-rank stock screener V2: no transactions, no risk decisions."""

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

from atlaspm.ai_stock_ranker_v2 import (
    _rank_queries, _rank_scores, train_stock_learning_to_rank, rank_stocks_v2,
)

try:
    import lightgbm
except ImportError:
    lightgbm = None


class AIStockRankerV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stocks = self.root / "stocks.csv"
        self.benchmark = self.root / "benchmark.csv"
        self.model = self.root / "model.txt"
        self.days = []
        d = date(2020, 1, 1)
        while d < date(2024, 1, 1):
            if d.weekday() < 5:
                self.days.append(d)
            d += timedelta(days=1)
        with self.stocks.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "symbol", "adjusted_close"])
            for i, day in enumerate(self.days):
                for j, name in enumerate("ABCDEF"):
                    drift = 0.00015 + j * 0.00008
                    cyclic = ((i * (j + 3)) % 43) / 1700
                    w.writerow([day.isoformat(), name, 100 * (1 + drift) ** i * (1 + cyclic)])
        with self.benchmark.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "symbol", "adjusted_close"])
            for i, day in enumerate(self.days):
                w.writerow([day.isoformat(), "SPY", 250 * (1.00031 ** i)])

    def _train(self):
        return train_stock_learning_to_rank(
            self.stocks, self.benchmark, self.model,
            start="2020-01-01", train_end="2021-12-31",
            validation_end="2022-12-30", test_end="2023-12-29",
            num_boost_round=50, min_train_rows=30, top=3,
        )

    def test_groupwise_relevance_is_within_cohort(self):
        sample = []
        for day in ("2021-01-04", "2021-02-01"):
            for i, name in enumerate("ABCDE"):
                sample.append({
                    "date": day, "symbol": name,
                    "forward_excess_return": i / 100,
                    "features": [float(i)] * 9,
                })
        rows, labels, groups = _rank_queries(sample)
        self.assertEqual(groups, [5, 5])
        self.assertEqual(len(rows), sum(groups))
        self.assertEqual(labels, [0, 1, 2, 3, 4] * 2)

    def test_rank_group_rejects_futureless_labels(self):
        with self.assertRaisesRegex(ValueError, "fully matured"):
            _rank_queries([{"date": "2021-01-04", "forward_excess_return": None}])

    def test_tied_rank_scores_not_false_return_percent(self):
        rows = [{"symbol": s} for s in "ABCDEF"]
        result, diag = _rank_scores(rows, [5, 4, 3, 3, 3, 2], top=4)
        self.assertEqual([r["rank"] for r in result], [1, 2, 3, 3])
        self.assertEqual(result[3]["tie_count"], 3)
        self.assertTrue(diag["ties_cross_top_n_cutoff"])
        self.assertEqual(diag["rank_quality"], "REVIEW_REQUIRED")
        self.assertIn("NOT a percent", diag["note"])
        self.assertNotIn("predicted_forward_excess_percent", result[0])
        _, no_ambiguity = _rank_scores(rows, [5, 4, 3, 3, 3, 2], top=5)
        self.assertFalse(no_ambiguity["ties_cross_top_n_cutoff"])

    def test_invalid_grouping_inputs(self):
        with self.assertRaisesRegex(ValueError, "Prediction length mismatch"):
            _rank_scores([{"symbol": "A"}], [], top=1)

    def test_cli_refuses_public_model_output(self):
        from atlaspm.cli import main
        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                outcome = main([
                    "stock-ai-train-v2",
                    "--prices", str(self.stocks),
                    "--benchmark-prices", str(self.benchmark),
                    "--model-output", "outside_local_data.txt",
                ])
            self.assertEqual(outcome, 2)
            self.assertFalse((self.root / "outside_local_data.txt").exists())
        finally:
            os.chdir(cwd)

    @unittest.skipIf(lightgbm is None, "Optional LightGBM dependency missing")
    def test_real_rank_xendcg_train_and_infer(self):
        trained = self._train()
        self.assertEqual(trained["method"], "cross_sectional_rank_xendcg_v2")
        self.assertEqual(trained["group_counts"]["train"] > 0, True)
        self.assertEqual(trained["group_counts"]["test"] > 0, True)
        self.assertIn("mean_spearman_ic", trained["validation"])
        self.assertIn("mean_spearman_ic", trained["test"])
        self.assertGreater(trained["split_counts"]["train"], 30)
        self.assertFalse(trained["live_execution_enabled"])
        self.assertTrue(self.model.exists())
        results = rank_stocks_v2(
            self.stocks, self.benchmark, self.model,
            as_of=self.days[-1].isoformat(), top=4,
        )
        self.assertEqual(results["scored_count"], 6)
        self.assertEqual(len(results["selected"]), 4)
        self.assertEqual(results["score_definition"],
                         "Ordinal LightGBM rank utility; NOT predicted % return")
        self.assertTrue(all("ranking_score" in row for row in results["selected"]))
        self.assertTrue(all("predicted_forward_excess_percent" not in row for row in results["selected"]))
        self.assertFalse(results["live_execution_enabled"])
        with self.assertRaisesRegex(ValueError, "validation cutoff"):
            rank_stocks_v2(
                self.stocks, self.benchmark, self.model,
                as_of="2022-12-30", top=4,
            )
        with self.assertRaisesRegex(ValueError, "benchmark"):
            rank_stocks_v2(
                self.stocks, self.benchmark, self.model,
                as_of=self.days[-1].isoformat(), benchmark_symbol="QQQ",
            )


if __name__ == "__main__":
    unittest.main()
