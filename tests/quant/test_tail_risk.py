"""Historical ES (CVaR) estimators and adaptive exposure limits."""

import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.tail_risk import historical_daily_expected_shortfall
from atlaspm.risk_backtest import backtest_risk_budget, choose_budgeted_exposure


def tail_rows():
    return [[-.10, -.10], [-.08, -.08]] + [[.01, .02]] * 38


class TailRiskTests(unittest.TestCase):
    def test_worst_five_percent_mean_is_nine_percent(self):
        result = historical_daily_expected_shortfall(
            tail_rows(), [.5, .5], tail_fraction=.05
        )
        self.assertEqual(result["historical_days"], 40)
        self.assertEqual(result["tail_days"], 2)
        self.assertAlmostEqual(result["daily_expected_shortfall_fraction"], .09)
        self.assertAlmostEqual(result["worst_observed_daily_loss"], .10)

    def test_exposure_cap_can_introduce_cash(self):
        settings = dict(
            maximum_requested_leverage=2,
            max_annual_volatility=.30,
            stress_decline=.30,
            max_stress_loss=.40,
            maintenance_fraction=.30,
            minimum_maintenance_buffer=.05,
            annual_financing_rate=.065,
            max_annual_financing_cost=.08,
        )
        regular = choose_budgeted_exposure(
            {"A": .5, "B": .5}, [[.04, 0], [0, .04]], **settings
        )
        tail = choose_budgeted_exposure(
            {"A": .5, "B": .5}, [[.04, 0], [0, .04]],
            daily_training_returns=tail_rows(),
            max_daily_tail_loss=.02, tail_fraction=.05, **settings
        )
        self.assertGreater(regular["selected_gross_exposure"], 1)
        self.assertAlmostEqual(tail["selected_gross_exposure"], .02 / .09)
        self.assertGreater(tail["cash_weight"], .77)
        self.assertAlmostEqual(tail["historical_daily_es_at_selected_exposure"], .02)

    def test_estimator_uses_initialized_symbol_weights(self):
        # Regression: an optional ES cap must use the supplied risky-sleeve
        # weights, not reference a local list before it is initialized.
        sample = [[-.12, 0.0]] + [[0.01, -0.01]] * 39
        base = dict(
            maximum_requested_leverage=2,
            max_annual_volatility=.30,
            stress_decline=.30,
            max_stress_loss=.40,
            maintenance_fraction=.30,
            minimum_maintenance_buffer=.05,
            annual_financing_rate=.065,
            max_annual_financing_cost=.08,
            daily_training_returns=sample,
            max_daily_tail_loss=.015,
            tail_fraction=.05,
        )
        result = choose_budgeted_exposure(
            {"A": .75, "B": .25}, [[.04, 0.0], [0.0, .04]],
            **base
        )
        self.assertAlmostEqual(
            result["historical_daily_tail_risk"]["daily_expected_shortfall_fraction"],
            .0425, places=8
        )
        self.assertAlmostEqual(
            result["selected_gross_exposure"], .015 / .0425, places=8
        )
        self.assertAlmostEqual(
            result["historical_daily_es_at_selected_exposure"], .015, places=8
        )

    def test_no_budget_keeps_old_behavior(self):
        settings = dict(
            maximum_requested_leverage=2,
            max_annual_volatility=.30,
            stress_decline=.30,
            max_stress_loss=.40,
            maintenance_fraction=.30,
            minimum_maintenance_buffer=.05,
            annual_financing_rate=.065,
            max_annual_financing_cost=.08,
        )
        first = choose_budgeted_exposure(
            {"A": .5, "B": .5}, [[.04, 0], [0, .04]], **settings
        )
        second = choose_budgeted_exposure(
            {"A": .5, "B": .5}, [[.04, 0], [0, .04]],
            daily_training_returns=tail_rows(), max_daily_tail_loss=None,
            **settings
        )
        self.assertEqual(first["selected_gross_exposure"], second["selected_gross_exposure"])
        self.assertIsNone(second["historical_daily_tail_risk"])

    def test_invalid_tail_inputs_fail(self):
        with self.assertRaisesRegex(ValueError, "tail_fraction"):
            historical_daily_expected_shortfall(tail_rows(), [.5, .5], tail_fraction=0)
        with self.assertRaisesRegex(ValueError, "row width"):
            historical_daily_expected_shortfall([[0.01]] * 40, [.5, .5])
        with self.assertRaisesRegex(ValueError, "At least 20"):
            historical_daily_expected_shortfall(tail_rows()[:10], [.5, .5])
        with self.assertRaisesRegex(ValueError, "required"):
            choose_budgeted_exposure(
                {"A": .5, "B": .5}, [[.04, 0], [0, .04]],
                maximum_requested_leverage=2, max_annual_volatility=.30,
                stress_decline=.30, max_stress_loss=.40,
                maintenance_fraction=.30, minimum_maintenance_buffer=.05,
                annual_financing_rate=.065, max_annual_financing_cost=.08,
                max_daily_tail_loss=.03,
            )

    def test_full_rolling_uses_only_trailing_returns(self):
        def fixture(directory, last_day_multiplier):
            p = Path(directory) / "prices.csv"
            start = date(2024, 1, 1)
            with p.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["date", "symbol", "adjusted_close"])
                for i in range(120):
                    day = (start + timedelta(days=i)).isoformat()
                    for ticker, base, slope in [("A", 100, .20), ("B", 90, .13)]:
                        v = base + i * slope + (i % 7) * .45
                        if i == 119:
                            v *= last_day_multiplier
                        writer.writerow([day, ticker, v])
            return p

        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            baseline = backtest_risk_budget(
                fixture(a, 1.0), lookback_days=40, rebalance_days=21,
                max_daily_tail_loss=.008
            )
            changed = backtest_risk_budget(
                fixture(b, 1.10), lookback_days=40, rebalance_days=21,
                max_daily_tail_loss=.008
            )
        self.assertTrue(baseline["strategy"]["tail_risk_budget_active"])
        self.assertAlmostEqual(
            baseline["rebalances"][0]["gross_exposure_at_execution"],
            changed["rebalances"][0]["gross_exposure_at_execution"]
        )
        self.assertNotEqual(
            baseline["strategy"]["ending_equity_index"],
            changed["strategy"]["ending_equity_index"]
        )
        for decision in baseline["rebalances"]:
            diag = decision["historical_daily_tail_risk"]
            self.assertEqual(diag["historical_days"], 40)
            self.assertLess(decision["training_last_date"], decision["execution_date"])
            self.assertLessEqual(decision["historical_daily_es_at_selected_exposure"], .008 + 1e-9)


if __name__ == "__main__":
    unittest.main()
