"""Verify selection timing, mark-to-market and paid hedge costs."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atlaspm.quant.option_history import OptionHistory
from atlaspm.quant.protective_put import ProtectivePutSettings, compare_protective_put, load_stock_marks


class ProtectivePutTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.contracts = root / "contracts.csv"
        self.contracts.write_text(
            "contract_id,underlying,right,strike,last_trade_at,expiration_at,multiplier,exercise_style,settlement_type,exercise_threshold_usd\n"
            "A,SPY,put,95,2024-02-02T21:00Z,2024-02-02T22:00Z,100,american,physical,1\n"
            "B,SPY,put,85,2024-02-09T21:00Z,2024-02-09T22:00Z,100,american,physical,1\n", encoding="utf-8",
        )
        self.quotes = root / "quotes.csv"
        self.quotes.write_text(
            "contract_id,quote_at,bid,ask,bid_size,ask_size\n"
            "A,2024-01-08T21:00Z,1,1.1,5,5\nA,2024-01-09T21:00Z,2,2.2,5,5\n"
            "B,2024-01-09T21:00Z,0.5,0.6,5,5\nA,2024-01-10T21:00Z,6,6.2,5,5\n"
            "B,2024-01-10T21:00Z,2,2.1,5,5\nA,2024-01-11T21:00Z,4.5,4.6,5,5\n"
            "B,2024-01-11T21:00Z,2.4,2.5,5,5\nB,2024-01-12T21:00Z,2.1,2.2,5,5\n", encoding="utf-8",
        )
        self.settlements = root / "settlements.csv"
        self.settlements.write_text("contract_id,reference_at,available_at,cash_at,settlement_value\n", encoding="utf-8")
        marks = root / "stock_marks.csv"
        marks.write_text(
            "observed_at,close,sale_cash_at,dividend_per_share\n"
            "2024-01-08T21:00Z,100,2024-01-09T14:30Z,0\n2024-01-09T21:00Z,98,2024-01-10T14:30Z,0\n"
            "2024-01-10T21:00Z,90,2024-01-11T14:30Z,1\n2024-01-11T21:00Z,92,2024-01-12T14:30Z,0\n"
            "2024-01-12T21:00Z,96,2024-01-16T14:30Z,0\n", encoding="utf-8",
        )
        self.marks = load_stock_marks(marks)

    def _run(self, cash=500):
        settings = ProtectivePutSettings("SPY", 100, cash, roll_sessions=2, target_dte=30)
        return compare_protective_put(OptionHistory(self.contracts, self.quotes, self.settlements), self.marks, settings)

    def test_next_session_roll_and_matched_net_asset_values(self):
        report = self._run()
        decisions = report["decisions"]
        self.assertEqual([row["contract_id"] for row in decisions], ["A", "B"])
        fills = report["option_replay"]["fills"]
        self.assertEqual([(row["side"], row["fill_price"]) for row in fills],
                         [("buy", 2.2), ("sell", 4.5), ("buy", 2.5), ("sell", 2.1)])
        self.assertEqual(report["option_replay"]["execution_fees_usd"], 4)
        curve = report["curve"]
        self.assertEqual([row["covered_shares"] for row in curve], [0, 100, 100, 100, 0])
        self.assertEqual(curve[0]["hedged_nav_usd"], curve[0]["unhedged_nav_usd"])
        self.assertEqual(curve[3]["option_sale_receivable_usd"], 450)
        self.assertAlmostEqual(curve[3]["cash_available_usd"], 27)
        self.assertAlmostEqual(report["hedged"]["ending_nav_usd"], 10386)
        self.assertEqual(report["unhedged"]["ending_nav_usd"], 10200)
        self.assertGreater(report["hedged"]["max_drawdown"], report["unhedged"]["max_drawdown"])

    def test_does_not_switch_after_execution_quote_disappears(self):
        original = self.quotes.read_text(encoding="utf-8")
        self.quotes.write_text(original.replace("A,2024-01-09T21:00Z,2,2.2,5,5\n", ""), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "No fresh option quote for A"):
            self._run()

    def test_roll_cannot_fund_buy_with_unsettled_sale(self):
        with self.assertRaisesRegex(ValueError, "Insufficient cash for option purchase: B"):
            self._run(cash=472)
