"""Offline regression tests for time-aligned risk-managed cash/margin simulation."""

import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.risk_backtest import (
    backtest_risk_budget, choose_budgeted_exposure, _trade_to_risk_budget
)


def write_prices(directory: str, change_last: bool = False) -> Path:
    path = Path(directory) / "fake_history.csv"
    first = date(2021, 1, 1)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "symbol", "adjusted_close"])
        for i in range(130):
            day = (first + timedelta(days=i)).isoformat()
            a = 100 + i * .14 + .4 * (i % 5)
            b = 120 + i * .13 + .5 * (i % 7)
            if change_last and i == 129:
                b *= 1.12
            writer.writerow([day, "A", a])
            writer.writerow([day, "B", b])
    return path


class AdaptiveBudgetTest(unittest.TestCase):
    def test_cash_and_loan_ledger(self):
        holdings, cash, loan, fee, turnover = _trade_to_risk_budget(
            [0.0, 0.0], 1.0, [.5, .5], .5, .001
        )
        self.assertAlmostEqual(sum(holdings) + cash - loan, 1 - fee, places=12)
        self.assertGreater(cash, 0)
        self.assertEqual(loan, 0)
        h2, c2, d2, f2, _ = _trade_to_risk_budget(
            [0.0, 0.0], 1.0, [.5, .5], 1.5, .001
        )
        self.assertEqual(c2, 0)
        self.assertGreater(d2, 0)
        self.assertAlmostEqual(sum(h2) + c2 - d2, 1 - f2, places=12)

    def test_shock_cap_is_binding_at_two_times(self):
        policy = choose_budgeted_exposure(
            {"A": .5, "B": .5}, [[.04, 0], [0, .04]],
            maximum_requested_leverage=2, max_annual_volatility=.30,
            stress_decline=.30, max_stress_loss=.40,
            maintenance_fraction=.30, minimum_maintenance_buffer=.05,
            annual_financing_rate=.065, max_annual_financing_cost=.08,
        )
        self.assertAlmostEqual(policy["selected_gross_exposure"], 4 / 3, places=10)
        self.assertAlmostEqual(policy["uniform_stress_equity_fraction"], .6, places=10)
        self.assertGreaterEqual(policy["uniform_stress_maintenance_excess_fraction"], .05)

    def test_tight_volatility_limit_introduces_cash(self):
        policy = choose_budgeted_exposure(
            {"A": .5, "B": .5}, [[.04, 0], [0, .04]],
            maximum_requested_leverage=2, max_annual_volatility=.07,
            stress_decline=.30, max_stress_loss=.40,
            maintenance_fraction=.30, minimum_maintenance_buffer=.05,
            annual_financing_rate=.065, max_annual_financing_cost=.08,
        )
        self.assertLess(policy["selected_gross_exposure"], 1)
        self.assertGreater(policy["cash_weight"], 0)
        self.assertAlmostEqual(policy["annual_borrowing_cost_fraction"], 0)

    def test_full_walkforward_and_reconciliation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_prices(folder)
            data = backtest_risk_budget(
                path, lookback_days=40, rebalance_days=21,
                max_leverage=2, max_annual_volatility=.07, fee_bps=5,
            )
        self.assertEqual(data["data"]["backtest_daily_observations"], 88)
        self.assertEqual(data["strategy"]["rebalance_count"], 5)
        self.assertGreater(data["strategy"]["cash_allocation_rebalance_count"], 0)
        self.assertEqual(data["strategy"]["margin_allocation_rebalance_count"], 0)
        self.assertGreater(data["strategy"]["ending_equity_index"], 0)
        for point in data["equity_curve"]:
            self.assertAlmostEqual(
                point["gross_exposure_equity_index_units"]
                + point["cash_equity_index_units"]
                - point["loan_equity_index_units"],
                point["equity_index"], places=10,
            )
        for decision in data["rebalances"]:
            self.assertLess(decision["signal_date"], decision["execution_date"])
            self.assertLess(decision["execution_date"], decision["first_held_return_date"])
            self.assertLessEqual(decision["estimated_total_annual_volatility"], .07 + 1e-9)

    def test_future_price_does_not_change_earlier_allocation(self):
        with tempfile.TemporaryDirectory() as folder1, tempfile.TemporaryDirectory() as folder2:
            plain = backtest_risk_budget(
                write_prices(folder1), lookback_days=40, rebalance_days=21
            )
            altered = backtest_risk_budget(
                write_prices(folder2, True), lookback_days=40, rebalance_days=21
            )
        self.assertEqual(
            plain["rebalances"][0]["weights"],
            altered["rebalances"][0]["weights"],
        )
        self.assertEqual(
            plain["rebalances"][0]["gross_exposure_at_execution"],
            altered["rebalances"][0]["gross_exposure_at_execution"],
        )
        self.assertNotEqual(
            plain["strategy"]["ending_equity_index"],
            altered["strategy"]["ending_equity_index"],
        )

    def test_invalid_budget_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_prices(folder)
            for kwargs in (
                {"max_annual_volatility": 0},
                {"max_leverage": 5},
                {"stress_decline": -1},
                {"lookback_days": 0},
                {"max_weight": .4},
            ):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    backtest_risk_budget(path, **({"lookback_days": 40} | kwargs))

    def test_all_cash_when_zero_allowed_stress_loss(self):
        policy = choose_budgeted_exposure(
            {"A": .5, "B": .5}, [[.04, 0], [0, .04]],
            maximum_requested_leverage=2, max_annual_volatility=.30,
            stress_decline=.30, max_stress_loss=0.0,
            maintenance_fraction=.30, minimum_maintenance_buffer=.05,
            annual_financing_rate=.065, max_annual_financing_cost=.08,
        )
        self.assertEqual(policy["selected_gross_exposure"], 0.0)
        self.assertEqual(policy["cash_weight"], 1.0)


if __name__ == "__main__":
    unittest.main()
