"""Offline regression tests for next-close walk-forward stock screens."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from importlib.util import find_spec
from pathlib import Path

from atlaspm.engine.stock_allocation import StockAllocationSettings
from atlaspm.research.stock_history_audit import audit_stock_history
from atlaspm.research.stock_walkforward import backtest_stock_screener


class StockWalkforwardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.stocks = self.base / "stocks.csv"
        self.benchmark = self.base / "benchmark.csv"
        self.fundamentals = self.base / "fundamentals.csv"
        self.universe = self.base / "universe.csv"
        self.delistings = self.base / "delistings.csv"
        self.dates = []
        day = date(2024, 1, 1)
        while len(self.dates) < 370:
            if day.weekday() < 5:
                self.dates.append(day)
            day += timedelta(days=1)
        self.start = self.dates[260].isoformat()
        self.end = self.dates[355].isoformat()
        self._write_prices()

    def _write_prices(self, missing=None, last_trade=None):
        with self.stocks.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "symbol", "adjusted_close"])
            for i, day in enumerate(self.dates):
                for symbol, value in [
                    ("A", 100 * 1.001 ** i),
                    ("B", 70 * 1.0002 ** i),
                    ("C", 90 * 0.9998 ** i),
                ]:
                    if last_trade is not None and symbol == last_trade[0] and day > last_trade[1]:
                        continue
                    if (symbol, day) != missing:
                        writer.writerow([day.isoformat(), symbol, value])
        with self.benchmark.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "symbol", "adjusted_close"])
            for i, day in enumerate(self.dates):
                writer.writerow([day.isoformat(), "SPY", 200 * 1.0003 ** i])

    def _run(self, **kwargs):
        arguments = {"start": self.start, "end": self.end, "top": 2, **kwargs}
        return backtest_stock_screener(self.stocks, self.benchmark, **arguments)

    def _write_universe(self, rows):
        with self.universe.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["as_of_date", "symbol"])
            writer.writerows(rows)

    def _write_delisting(self, last_trade, cash_date, terminal_return):
        with self.delistings.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows([
                ["symbol", "last_trade_date", "cash_date", "return_from_last_close"],
                ["A", last_trade, cash_date, terminal_return],
            ])

    @unittest.skipUnless(find_spec("scipy"), "Optional portfolio dependency")
    def test_quant_same_picks_and_reconciled_costs_under_sector_and_turnover_limits(self):
        self._write_fundamentals(later_snapshots=True)
        settings = StockAllocationSettings(max_weight=.50, sector_cap=.65, turnover_limit=.45)
        result = self._run(quant_settings=settings, fundamentals_path=self.fundamentals)
        quant = result["quant"]
        self.assertEqual(quant["portfolio"]["return_observations"], result["portfolio"]["return_observations"])
        self.assertTrue(quant["executions"])
        self.assertIn("TURNOVER_LIMIT", quant["executions"][0]["binding_constraints"])
        executed = 0
        for reference, trade in zip(result["executions"], quant["executions"], strict=True):
            self.assertEqual(reference["selected"], trade["selected"])
            self.assertEqual(reference["execution_date"], trade["execution_date"])
            if trade["status"] != "OPTIMIZED":
                self.assertEqual(trade["cost_index"], 0)
                self.assertEqual(trade["post_trade_liquid_weights"], trade["pre_trade_liquid_weights"])
                continue
            executed += 1
            weights = trade["post_trade_liquid_weights"]
            self.assertTrue(set(weights) <= set(reference["selected"]))
            self.assertLessEqual(max(weights.values(), default=0), .50000001)
            self.assertLessEqual(weights.get("A", 0) + weights.get("B", 0), .65000001)
            self.assertLessEqual(trade["turnover_over_liquid_equity"], .45000001)
            before = trade["liquid_equity_before"]
            after = trade["liquid_equity_after"]
            self.assertAlmostEqual(before - after, trade["cost_index"])
            self.assertAlmostEqual(after * (1 - sum(weights.values())), trade["cash_after"])
            turnover_cost = trade["turnover_over_liquid_equity"] * before * .001
            self.assertAlmostEqual(turnover_cost, trade["cost_index"])
        self.assertGreater(executed, 1)

    def test_delisting_proceeds_are_unavailable_until_booked_and_can_be_zero(self):
        last_trade, cash_date = self.dates[270], self.dates[287]
        self._write_prices(last_trade=("A", last_trade))
        self._write_universe([(self.start, "A"), (self.dates[281], "B")])
        options = {"universe_history_path": self.universe, "max_universe_age_days": 110,
                   "delistings_path": self.delistings, "fee_bps": 0, "slippage_bps": 0}
        self._write_delisting(last_trade, cash_date, -.5)
        half = self._run(**options)
        self._write_delisting(last_trade, cash_date, -1)
        zero = self._run(**options)
        settled = half["delisting_settlements"][0]

        self.assertEqual(settled["booked_at_close"], cash_date.isoformat())
        self.assertAlmostEqual(settled["cash_proceeds_index"], settled["carrying_value_index"] * .5)
        self.assertEqual(half["delisting_settlements"], half["equal_weight_delisting_settlements"])
        before_cash = self.dates.index(cash_date) - 260
        self.assertEqual(half["equity_curve"][:before_cash], zero["equity_curve"][:before_cash])
        rebalance = half["equity_curve"][282 - 260]
        self.assertEqual(rebalance["holdings_count"], 0)
        self.assertAlmostEqual(rebalance["delisting_receivable_index"], rebalance["equity_index"])
        self.assertAlmostEqual(half["equity_curve"][before_cash]["cash_weight"], 1.0)
        self.assertEqual(zero["portfolio"]["total_return"], -1.0)
        self.assertEqual(zero["equal_weight_universe"]["max_drawdown"], -1.0)

    def test_delisting_between_signal_and_fill_keeps_unfilled_budget_as_cash(self):
        last_trade, cash_date = self.dates[260], self.dates[270]
        self._write_prices(last_trade=("A", last_trade))
        self._write_universe([(self.start, "A"), (self.start, "B"), (self.dates[281], "B")])
        self._write_delisting(last_trade, cash_date, -.5)
        result = self._run(universe_history_path=self.universe, max_universe_age_days=110,
                           delistings_path=self.delistings, fee_bps=0, slippage_bps=0)

        self.assertEqual(result["executions"][0]["unfilled_due_to_delisting"], ["A"])
        self.assertEqual(result["equal_weight_universe_executions"][0]["unfilled_due_to_delisting"], ["A"])
        self.assertAlmostEqual(result["equity_curve"][1]["cash_weight"], .5)
        self.assertAlmostEqual(result["equity_curve"][1]["universe_cash_weight"], .5)
        self.assertEqual(result["delisting_settlements"], [])

    def test_delisting_requires_matching_last_trade_and_real_marks_before_it(self):
        last_trade, cash_date = self.dates[270], self.dates[287]
        self._write_delisting(last_trade, cash_date, -1)
        with self.assertRaisesRegex(ValueError, "conflicts with a close after"):
            self._run(delistings_path=self.delistings)

        self._write_prices(missing=("A", self.dates[265]), last_trade=("A", last_trade))
        with self.assertRaisesRegex(ValueError, "MISSING_HELD_PRICE: A"):
            self._run(delistings_path=self.delistings)

    def test_historical_membership_drives_strategy_and_equal_weight_baseline(self):
        later_date = self.dates[281].isoformat()
        self._write_universe([
            (later_date, "B"), (later_date, "C"), (later_date, "SPY"),
            (self.start, "A"), (self.start, "SPY"),
        ])
        result = self._run(universe_history_path=self.universe, max_universe_age_days=100)
        early, later = result["decisions"][:2]

        self.assertEqual(result["parameters"]["universe_source"], "historical_snapshots")
        self.assertEqual(result["universe_count_excluding_benchmark"], 3)
        self.assertEqual(early["universe_snapshot_as_of"], self.start)
        self.assertEqual(later["universe_snapshot_as_of"], later_date)
        self.assertEqual([row["symbol"] for row in early["selected"]], ["A"])
        self.assertEqual({row["symbol"] for row in later["selected"]}, {"B", "C"})
        self.assertEqual([early["universe_member_count"], later["universe_member_count"]], [1, 2])
        self.assertEqual([row["holdings_count"] for row in result["equal_weight_universe_executions"][:2]],
                         [1, 2])
        self.assertAlmostEqual(result["portfolio"]["total_return"],
                               result["equal_weight_universe"]["total_return"])
        audit = audit_stock_history(self.stocks, self.benchmark, self.universe,
                                    start=self.start, end=self.end, max_universe_age_days=100)
        self.assertEqual(audit["status"], "DECISION_CLOSES_COMPLETE")
        self.assertEqual(
            [(row["decision_date"], row["next_close_date"]) for row in audit["decisions"]],
            [(row["date"], row["execution_date"]) for row in result["decisions"]],
        )

    def test_historical_membership_rejects_future_stale_and_duplicate_snapshots(self):
        invalid_cases = [
            ([(self.dates[261], "A")], {}, "No historical universe snapshot"),
            ([(self.start, "A")], {"max_universe_age_days": 0}, "snapshot .* is stale"),
            ([(self.start, "A"), (self.start, " a ")], {}, "Duplicate historical universe member"),
        ]
        for rows, options, error in invalid_cases:
            with self.subTest(error=error):
                self._write_universe(rows)
                with self.assertRaisesRegex(ValueError, error):
                    self._run(universe_history_path=self.universe, **options)

    def test_historical_membership_requires_its_decision_and_exit_prices(self):
        self._write_universe([(self.start, "A"), (self.start, "C")])
        self._write_prices(missing=("C", self.dates[260]))
        with self.assertRaisesRegex(ValueError, "MISSING_UNIVERSE_DECISION_CLOSE.*C"):
            self._run(universe_history_path=self.universe)

        self._write_universe([(self.start, "A"), (self.dates[281], "B")])
        self._write_prices(missing=("A", self.dates[282]))
        with self.assertRaisesRegex(ValueError, "MISSING_HELD_PRICE: A"):
            self._run(universe_history_path=self.universe)

    def test_history_audit_reports_all_gaps_in_one_cli_run(self):
        from atlaspm.cli import main
        self._write_universe([(self.dates[281], "A"), (self.dates[281], "C"), (self.dates[281], "SPY")])
        self._write_prices(missing=("C", self.dates[281]))
        cwd = Path.cwd()
        try:
            os.chdir(self.base)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "stock-history-audit", "--prices", str(self.stocks), "--benchmark-prices", str(self.benchmark),
                    "--universe-history", str(self.universe), "--start", self.start, "--end", self.end,
                    "--max-universe-age-days", "7",
                ])
            self.assertEqual(status, 0)
            summary = json.loads(output.getvalue())
            self.assertNotIn("decisions", summary)
            self.assertEqual(summary["status"], "REVIEW_REQUIRED")
            self.assertEqual(summary["decisions_without_usable_snapshot"], 4)
            self.assertEqual(summary["decision_close_coverage_pct_on_usable_snapshots"], 50.0)
            report = json.loads(Path(summary["written"]).read_text(encoding="utf-8"))
            early, missing_close, stale = report["decisions"][:3]
            self.assertIn("No historical universe snapshot", early["error"])
            self.assertEqual(missing_close["missing_decision_close_symbols"], ["C"])
            self.assertIn("is stale", stale["error"])
        finally:
            os.chdir(cwd)

    def _write_fundamentals(self, *, later_snapshots=False):
        with self.fundamentals.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["as_of_date", "symbol", "sector", "market_cap_usd", "roe",
                             "revenue_growth_yoy", "debt_to_equity", "fcf_yield", "ev_to_ebitda"])
            if later_snapshots:
                writer.writerow([self.dates[281], "A", "Tech", 3e9, .4, .3, .1, .15, 6])
                writer.writerow([self.dates[281], "C", "Energy", 3e9, .2, .1, .3, .08, 12])
            writer.writerow([self.dates[250], "A", "Tech", 1e9, .1, .1, .3, .06, 12])
            writer.writerow([self.dates[250], "B", "Tech", 3e9, .1, .05, .5, .04, 20])

    def test_fundamental_selection_uses_publication_dates_and_keeps_price_baseline(self):
        self._write_fundamentals()
        options = {"fundamentals_path": self.fundamentals, "minimum_market_cap_usd": 2e9,
                   "max_per_sector": 1, "max_fundamental_age_days": 80}
        original = self._run(**options)
        self._write_fundamentals(later_snapshots=True)
        result = self._run(**options)

        self.assertEqual(result["parameters"]["ranking_mode"], "fundamental_plus_technical")
        early, later = result["decisions"][:2]
        self.assertEqual(early, original["decisions"][0])
        self.assertEqual([row["symbol"] for row in early["selected"]], ["B"])
        self.assertEqual({row["symbol"] for row in later["selected"]}, {"A", "C"})
        self.assertEqual({row["fundamentals_as_of"] for row in later["selected"]}, {later["date"]})
        self.assertEqual(result["decisions"][-1]["selected"], [])
        self.assertEqual(result["equal_weight_universe"], self._run()["equal_weight_universe"])

    def test_date_range_and_benchmark(self):
        r = self._run()
        self.assertEqual(r["actual_window"], {"start": self.start, "end": self.end})
        self.assertEqual(r["decision_count"], 5)
        self.assertEqual(r["executed_rebalances"], 5)
        self.assertEqual(r["equity_curve"][0]["equity_index"], 1.0)
        self.assertEqual(r["equity_curve"][0]["spy_index"], 1.0)
        self.assertEqual(r["equity_curve"][0]["universe_index"], 1.0)
        self.assertEqual(r["equal_weight_universe"]["return_observations"], 95)
        self.assertGreater(r["benchmark"]["total_return"], 0)
        self.assertLessEqual(r["portfolio"]["max_drawdown"], 0)
        self.assertFalse(r["live_execution_enabled"])
        self.assertEqual(sum(v["sessions"] for v in r["yearly_returns"]),
                         len(r["equity_curve"]) - 1)

    def test_next_close_execution_no_same_close_profit(self):
        result = self._run(rebalance_days=1000, fee_bps=0, slippage_bps=0)
        self.assertEqual(result["executions"][0]["decision_date"], self.start)
        self.assertEqual(result["executions"][0]["execution_date"],
                         self.dates[261].isoformat())
        self.assertEqual(result["equity_curve"][0]["equity_index"], 1)
        self.assertAlmostEqual(result["equity_curve"][1]["equity_index"], 1)
        self.assertAlmostEqual(result["equity_curve"][1]["universe_index"], 1)
        self.assertGreater(result["equity_curve"][2]["equity_index"], 1)

    def test_turnover_costs_reduce_performance(self):
        free = self._run(fee_bps=0, slippage_bps=0)
        paid = self._run(fee_bps=10, slippage_bps=10)
        self.assertGreater(free["portfolio"]["total_return"],
                           paid["portfolio"]["total_return"])
        self.assertGreater(paid["execution_summary"]["total_modeled_transaction_cost_as_fraction_of_initial_equity"], 0)
        self.assertEqual(free["execution_summary"]["total_modeled_transaction_cost_as_fraction_of_initial_equity"], 0)
        self.assertGreater(free["equal_weight_universe"]["total_return"],
                           paid["equal_weight_universe"]["total_return"])
        self.assertGreater(paid["equal_weight_universe_execution_summary"]["sum_of_costs_in_normalized_initial_equity_units"], 0)

    def test_future_rows_cannot_change_past_backtest(self):
        previous = self._run()
        future = (self.dates[-1] + timedelta(days=1)).isoformat()
        with self.stocks.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([future, "A", 999999999])
            writer.writerow([future, "B", 1])
        with self.benchmark.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([future, "SPY", 999999999])
        after = self._run()
        self.assertEqual(previous["equity_curve"], after["equity_curve"])
        self.assertEqual(previous["decisions"], after["decisions"])
        self.assertEqual(previous["portfolio"], after["portfolio"])
        self.assertEqual(previous["equal_weight_universe"], after["equal_weight_universe"])

    def test_missing_execution_price_fails_closed(self):
        self._write_prices(missing=("A", self.dates[261]))
        with self.assertRaisesRegex(ValueError, "MISSING_EXECUTION_PRICE"):
            self._run()

    def test_missing_held_price_fails_closed(self):
        self._write_prices(missing=("A", self.dates[263]))
        with self.assertRaisesRegex(ValueError, "MISSING_HELD_PRICE"):
            self._run()

    def test_equal_weight_universe_independent_of_top_n(self):
        one = self._run(top=1)
        two = self._run(top=2)
        self.assertEqual(one["equal_weight_universe"], two["equal_weight_universe"])
        self.assertEqual(one["equal_weight_universe_executions"],
                         two["equal_weight_universe_executions"])
        self.assertNotEqual(one["portfolio"]["total_return"], two["portfolio"]["total_return"])
        self.assertEqual(one["equal_weight_universe_executions"][0]["holdings_count"], 3)
        self.assertAlmostEqual(one["relative"]["cagr_minus_equal_weight_universe_percentage_points"],
                               100 * (one["portfolio"]["cagr"] - one["equal_weight_universe"]["cagr"]))

    def test_missing_universe_execution_price_fails_closed(self):
        self._write_prices(missing=("C", self.dates[261]))
        with self.assertRaisesRegex(ValueError, "MISSING_UNIVERSE_EXECUTION_PRICE"):
            self._run(top=1)

    def test_missing_universe_held_price_fails_closed(self):
        self._write_prices(missing=("C", self.dates[263]))
        with self.assertRaisesRegex(ValueError, "MISSING_UNIVERSE_HELD_PRICE"):
            self._run(top=1)

    def test_input_validation(self):
        with self.assertRaisesRegex(ValueError, "end must be later"):
            self._run(start="2028-01-01")
        with self.assertRaisesRegex(ValueError, "rebalance-days"):
            self._run(rebalance_days=0)
        with self.assertRaisesRegex(ValueError, "fee-bps"):
            self._run(fee_bps=-1)

    def test_cli_stores_private_json_only(self):
        from atlaspm.cli import main
        self._write_fundamentals(later_snapshots=True)
        self._write_universe([(self.start, "A"), (self.start, "B"), (self.start, "C")])
        cwd = Path.cwd()
        try:
            os.chdir(self.base)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main(["stock-backtest", "--prices", str(self.stocks),
                             "--benchmark-prices", str(self.benchmark),
                             "--start", self.start, "--end", self.end,
                             "--fundamentals", str(self.fundamentals), "--min-market-cap", "2e9",
                             "--max-per-sector", "1", "--max-fundamental-age-days", "80",
                             "--universe-history", str(self.universe), "--max-universe-age-days", "140",
                             "--top", "2", "--output", "local_data/walkforward.json"])
            self.assertEqual(code, 0)
            summary = json.loads(stream.getvalue())
            saved = json.loads((self.base / "local_data/walkforward.json").read_text())
            self.assertEqual(summary["ranking_mode"], "fundamental_plus_technical")
            self.assertEqual(summary["universe_source"], "historical_snapshots")
            self.assertEqual(saved["parameters"]["max_fundamental_age_days"], 80)
            self.assertEqual(saved["executions"][0]["selected"], ["B"])
            self.assertEqual(summary["decision_count"], saved["decision_count"])
            self.assertEqual(summary["portfolio"]["cagr"], saved["portfolio"]["cagr"])
            self.assertFalse(saved["live_execution_enabled"])
            self.assertEqual(summary["equal_weight_universe"]["cagr"],
                             saved["equal_weight_universe"]["cagr"])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                status = main(["stock-backtest", "--prices", str(self.stocks),
                               "--benchmark-prices", str(self.benchmark),
                               "--start", self.start, "--end", self.end,
                               "--output", "public_results.json"])
            self.assertEqual(status, 2)
            self.assertFalse((self.base / "public_results.json").exists())
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
