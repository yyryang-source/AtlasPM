"""Deterministic correctness checks for cash and margin accounting."""

import unittest

from atlaspm.models import PortfolioAccount, Position
from atlaspm.portfolio import analyze_accounts, parse_accounts


class PortfolioTest(unittest.TestCase):
    def test_margin_leverage_and_loss(self) -> None:
        account = PortfolioAccount(
            name="margin",
            account_type="margin",
            cash=0,
            debt=600_000,
            positions=(Position("QQQ", 800_000, 0.25),),
        )
        self.assertEqual(account.equity, 200_000)
        self.assertEqual(account.gross_leverage, 4.0)
        self.assertEqual(account.maintenance_excess, 0)
        scenario = account.stress(0.10)
        self.assertAlmostEqual(scenario["equity"], 120_000)
        self.assertAlmostEqual(scenario["maintenance_deficit"], 60_000)
        self.assertTrue(scenario["below_maintenance"])

    def test_cash_cannot_borrow(self) -> None:
        with self.assertRaises(ValueError):
            PortfolioAccount("cash", "cash", 100, 1, ())

    def test_invalid_maintenance_rate(self) -> None:
        with self.assertRaises(ValueError):
            Position("QQQ", 100, 1.1)

    def test_invalid_negative_value(self) -> None:
        with self.assertRaises(ValueError):
            Position("QQQ", -100)

    def test_report_totals_and_apr(self) -> None:
        accounts = parse_accounts(
            {
                "accounts": [
                    {
                        "name": "cash", "account_type": "cash",
                        "cash": 50, "debt": 0, "positions": [
                            {"symbol": "KO", "market_value": 50}
                        ],
                    },
                    {
                        "name": "margin", "account_type": "margin",
                        "cash": 0, "debt": 50, "positions": [
                            {"symbol": "QQQ", "market_value": 150}
                        ],
                    },
                ]
            }
        )
        report = analyze_accounts(accounts, annual_margin_rate=0.10)
        self.assertEqual(report["summary"]["total_equity"], 200)
        self.assertEqual(report["summary"]["borrowed"], 50)
        self.assertEqual(report["summary"]["annual_borrowing_cost_estimate"], 5)

    def test_reject_duplicate_account_names(self) -> None:
        row = {
            "name": "A", "account_type": "cash", "cash": 100,
            "debt": 0, "positions": [],
        }
        with self.assertRaises(ValueError):
            parse_accounts({"accounts": [row, row]})

    def test_reject_impossible_declines(self) -> None:
        acct = PortfolioAccount("cash", "cash", 100, 0, ())
        with self.assertRaises(ValueError):
            analyze_accounts([acct], declines=[1.5])


if __name__ == "__main__":
    unittest.main()
