"""Treasury cash entitlement, clean pricing and rate exposure regressions."""

from datetime import date
from math import exp
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atlaspm.data.treasury_history import TreasuryHistory
from atlaspm.strategies.bonds.treasury_replay import replay_treasury_holding


class TreasuryReplayTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.contracts = root / "contracts.csv"
        self.contracts.write_text(
            "cusip,kind,dated_on,issued_on,maturity,annual_coupon_rate,face_increment_usd\n"
            "999999990,note,2023-12-31,2024-01-02,2024-12-31,0.04,100\n"
            "999999991,bill,2024-06-01,2024-06-03,2024-07-01,0,100\n", encoding="utf-8",
        )
        self.payments = root / "payments.csv"
        self.payments.write_text(
            "cusip,scheduled_on,paid_on\n999999990,2024-06-30,2024-07-01\n"
            "999999990,2024-12-31,2024-12-31\n999999991,2024-07-01,2024-07-01\n", encoding="utf-8",
        )
        self.quotes = root / "quotes.csv"
        self.quotes.write_text(
            "cusip,observed_on,clean_close_per_100\n999999990,2024-06-28,99.5\n"
            "999999990,2024-07-01,99.6\n999999990,2024-12-30,99.98\n999999991,2024-06-28,99.96\n",
            encoding="utf-8",
        )

    def _history(self):
        return TreasuryHistory(self.contracts, self.payments, self.quotes)

    def test_receivable_accrued_coupon_principal_and_rate_shock(self):
        days = [date.fromisoformat(text) for text in ("2024-06-28", "2024-06-30", "2024-07-01",
                                                    "2024-12-30", "2024-12-31")]
        replay = replay_treasury_holding(self._history(), "999999990", 10000, days, initial_cash_usd=100)
        open_mark, due, paid, last_trading_day, maturity = replay["valuations"]
        self.assertAlmostEqual(open_mark["accrued_interest_usd"], 200 * 180 / 182)
        self.assertEqual(due["quote_date"], "2024-06-28")
        self.assertEqual(due["accrued_interest_usd"], 0)
        self.assertEqual(due["payment_receivable_usd"], 200)
        self.assertEqual(due["cash_available_usd"], 100)
        self.assertEqual(paid["payment_receivable_usd"], 0)
        self.assertEqual(paid["cash_available_usd"], 300)
        self.assertAlmostEqual(last_trading_day["rate_shock_pnl_usd"],
                               last_trading_day["dirty_market_value_usd"] * (exp(-0.01 / 365) - 1))
        self.assertGreater(last_trading_day["dv01_usd"], 0)
        self.assertEqual(maturity["held_face_usd"], 0)
        self.assertEqual(maturity["dv01_usd"], 0)
        self.assertEqual(maturity["cash_available_usd"], 10500)
        self.assertEqual(maturity["coupon_paid_to_date_usd"], 400)
        self.assertEqual(maturity["principal_paid_to_date_usd"], 10000)
        for mark in replay["valuations"]:
            self.assertAlmostEqual(mark["nav_usd"], mark["cash_available_usd"] + mark["payment_receivable_usd"]
                                   + mark["dirty_market_value_usd"])

    def test_zero_coupon_bill_and_future_price_rejection(self):
        history = self._history()
        due = date(2024, 7, 1)
        with self.assertRaisesRegex(ValueError, "No fresh Treasury"):
            replay_treasury_holding(history, "999999991", 1000, [date(2024, 6, 27), due])
        result = replay_treasury_holding(history, "999999991", 1000, [date(2024, 6, 28), due])
        self.assertEqual(result["valuations"][0]["accrued_interest_usd"], 0)
        self.assertEqual(result["cashflows"][0]["coupon_usd"], 0)
        self.assertEqual(result["valuations"][-1]["nav_usd"], 1000)

    def test_opening_after_coupon_entitlement_excludes_that_receipt(self):
        result = replay_treasury_holding(self._history(), "999999990", 10000, [date(2024, 6, 30), date(2024, 7, 1)],
                                         include_opening_date_payment=False)
        self.assertEqual(result["cashflows"], [])
        self.assertTrue(all(row["cash_available_usd"] == row["payment_receivable_usd"] == 0
                            for row in result["valuations"]))

    def test_rejects_incomplete_schedule_and_nonstandard_face(self):
        with self.assertRaisesRegex(ValueError, "Invalid Treasury holding face"):
            replay_treasury_holding(self._history(), "999999990", 10005, [date(2024, 6, 28)])
        content = self.payments.read_text(encoding="utf-8")
        self.payments.write_text(content.replace("999999990,2024-06-30,2024-07-01\n", ""), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Expected complete regular Treasury payment schedule"):
            self._history()
