"""Exact-match historical active-risk statistics, with no live network."""

import unittest

from atlaspm.monitoring.performance import historical_spy_diagnostics


def index_from_returns(returns):
    equity = 1.0
    result = []
    for day in returns:
        equity *= 1 + day
        result.append(equity)
    return result


class PerformanceTests(unittest.TestCase):
    def test_known_beta_and_correlation(self):
        spy = [0.01, -0.02, 0.03, -0.01, 0.02]
        cash = [2 * change for change in spy]
        margin = [3 * change for change in spy]
        days = ["2023-12-28", "2023-12-29", "2024-01-02",
                "2024-01-03", "2025-01-02"]
        r = historical_spy_diagnostics(
            days, index_from_returns(cash),
            index_from_returns(margin), index_from_returns(spy)
        )
        self.assertAlmostEqual(r["full_period"]["cash"]["beta_to_spy"], 2.0)
        self.assertAlmostEqual(r["full_period"]["margin"]["beta_to_spy"], 3.0)
        self.assertAlmostEqual(r["full_period"]["cash"]["daily_correlation_to_spy"], 1.0)
        self.assertGreater(r["full_period"]["cash"]["annual_tracking_error"], 0)
        self.assertEqual(len(r["calendar_years"]), 3)
        self.assertTrue(r["calendar_years"][0]["partial_year"])
        self.assertFalse(r["calendar_years"][1]["partial_year"])
        self.assertTrue(r["calendar_years"][2]["partial_year"])
        self.assertEqual(r["complete_years_count"], 1)

    def test_zero_tracking_error_yields_null_ratio(self):
        prices = index_from_returns([.01, -.02, .03, -.04])
        r = historical_spy_diagnostics(
            ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
            prices, prices, prices,
        )
        self.assertAlmostEqual(r["full_period"]["cash"]["annual_tracking_error"], 0.0)
        self.assertIsNone(r["full_period"]["cash"]["information_ratio_zero_risk_free_assumption"])
        self.assertAlmostEqual(r["full_period"]["cash"]["cagr_difference_vs_spy"], 0.0)

    def test_calendar_years_are_compounded_and_excess_is_difference(self):
        dates = ["2023-12-29", "2024-01-02", "2024-01-03", "2025-01-02"]
        cash = index_from_returns([.01, .10, -.05, .02])
        margin = index_from_returns([.02, .08, -.01, .01])
        spy = index_from_returns([.01, .03, -.02, .01])
        r = historical_spy_diagnostics(dates, cash, margin, spy)
        year = next(row for row in r["calendar_years"] if row["year"] == 2024)
        self.assertAlmostEqual(year["cash_return"], (1.10 * .95) - 1)
        self.assertAlmostEqual(year["spy_return"], (1.03 * .98) - 1)
        self.assertAlmostEqual(
            year["cash_excess_return_vs_spy"],
            year["cash_return"] - year["spy_return"],
        )

    def test_invalid_data_rejected(self):
        dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
        okay = [1.0, 1.01, 1.03]
        with self.assertRaises(ValueError):
            historical_spy_diagnostics(dates, okay[:-1], okay, okay)
        with self.assertRaises(ValueError):
            historical_spy_diagnostics(dates, [1.0, -1, 1.02], okay, okay)
        with self.assertRaises(ValueError):
            historical_spy_diagnostics(dates[::-1], okay, okay, okay)


if __name__ == "__main__":
    unittest.main()
