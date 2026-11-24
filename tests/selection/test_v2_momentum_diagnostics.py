"""Offline tests for exact-cohort momentum extreme attribution."""

from __future__ import annotations

import contextlib
import io
import os
from datetime import date
from pathlib import Path
import tempfile
import unittest

from atlaspm.research.v2_momentum_diagnostics import _review_momentum_cohorts


class MomentumDiagnosticTests(unittest.TestCase):
    @staticmethod
    def rows(day, factors, forward_excess):
        return [
            {
                "date": day, "symbol": symbol,
                "features": [factor] + [0.0] * 8,
                "forward_excess_return": excess,
                "entry_date": "2026-07-02",
                "label_exit_date": "2026-08-03",
            }
            for symbol, factor, excess in zip("ABCDE", factors, forward_excess)
        ]

    def test_extreme_top_n_attribution_uses_provided_forward_labels(self):
        rows = self.rows(
            "2026-07-01", [5, 4, 3, 2, 1],
            [-0.9, -0.5, 0.0, 0.0, 0.0],
        )
        spy = {date(2026, 7, 2): 100.0, date(2026, 8, 3): 110.0}
        report = _review_momentum_cohorts(rows, spy, top=2)
        self.assertEqual(report["decision_dates"], 1)
        self.assertEqual(report["ambiguous_decision_dates"], 0)
        self.assertEqual(report["unambiguous_decision_dates"], 1)
        self.assertAlmostEqual(report["mean_top_n_forward_spy_excess"], -0.7)
        self.assertAlmostEqual(report["mean_top_n_minus_pool_mean"], -0.42)
        self.assertEqual(report["total_extreme_momentum_picks"], 2)
        self.assertEqual(report["total_forward_losses_over_50pct"], 1)
        cohort = report["cohorts"][0]
        self.assertEqual([v["symbol"] for v in cohort["selected"]], ["A", "B"])
        self.assertAlmostEqual(cohort["selected"][0]["forward_stock_return"], -0.8)
        self.assertAlmostEqual(cohort["top_n_outcome"]["spy_return_entry_to_exit"], 0.1)
        self.assertEqual(cohort["worst_five_selected"][0]["symbol"], "A")

    def test_boundary_ties_do_not_create_fake_selected_returns(self):
        rows = self.rows(
            "2026-07-01", [5, 5, 5, 2, 1],
            [0.1, 0.2, -0.8, 0.0, 0.0],
        )
        spy = {date(2026, 7, 2): 100.0, date(2026, 8, 3): 110.0}
        report = _review_momentum_cohorts(rows, spy, top=2)
        self.assertEqual(report["ambiguous_decision_dates"], 1)
        self.assertIsNone(report["mean_top_n_forward_spy_excess"])
        self.assertEqual(report["cohorts"][0]["selected"], [])
        self.assertIsNone(report["cohorts"][0]["top_n_outcome"])

    def test_missing_benchmark_price_rejected(self):
        rows = self.rows(
            "2026-07-01", [5, 4, 3, 2, 1],
            [0.1, 0.2, 0.0, 0.0, 0.0],
        )
        with self.assertRaisesRegex(ValueError, "Missing SPY benchmark"):
            _review_momentum_cohorts(rows, {}, top=2)

    def test_cli_rejects_outside_local_data_output_before_loading_csv(self):
        from atlaspm.cli import main
        with tempfile.TemporaryDirectory() as root:
            cwd = Path.cwd()
            try:
                os.chdir(root)
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    code = main([
                        "stock-ai-diagnose-momentum",
                        "--prices", "unavailable.csv",
                        "--benchmark-prices", "unavailable_spy.csv",
                        "--output", "outside.json",
                    ])
                self.assertEqual(code, 2)
                self.assertFalse((Path(root) / "outside.json").exists())
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
