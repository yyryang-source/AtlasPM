"""Covered European hedge payoff and funding boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atlaspm.data.option_history import OptionHistory
from atlaspm.strategies.options.hedge_scenarios import compare_expiry_hedges


class HedgeScenarioTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        self.terms=root/"terms.csv"
        self.quotes=root/"quotes.csv"
        self.settlements=root/"settlements.csv"
        self.terms.write_text("contract_id,underlying,right,strike,last_trade_at,expiration_at,multiplier,exercise_style,settlement_type,exercise_threshold_usd\n"
            "P,ABC,put,95,2025-02-01T20:00Z,2025-02-01T21:00Z,100,european,cash,0\n"
            "L,ABC,put,80,2025-02-01T20:00Z,2025-02-01T21:00Z,100,european,cash,0\n"
            "C,ABC,call,110,2025-02-01T20:00Z,2025-02-01T21:00Z,100,european,cash,0\n",encoding="utf-8")
        self.quotes.write_text("contract_id,quote_at,bid,ask,bid_size,ask_size\n"
            "P,2025-01-02T20:00Z,3,4,5,5\nL,2025-01-02T20:00Z,1,2,5,5\n"
            "C,2025-01-02T20:00Z,2,3,5,5\nP,2025-01-02T20:02Z,99,100,5,5\n",encoding="utf-8")
        self.settlements.write_text("contract_id,reference_at,available_at,cash_at,settlement_value\n",encoding="utf-8")

    def run_case(self,**kwargs):
        args={"as_of":"2025-01-02T20:01Z","spot":100,"spot_available_at":"2025-01-02T20:00Z",
              "shares":100,"settled_cash":1000,"minimum_cash":100,"long_put":"P","lower_put":"L",
              "covered_call":"C","terminal_spots":[0,70,100,130]}
        return compare_expiry_hedges(OptionHistory(self.terms,self.quotes,self.settlements),**{**args,**kwargs})

    def test_payoff_cost_and_coverage(self):
        r=self.run_case()
        a=r["arms"]
        self.assertEqual(r["quotes"]["P"]["ask"],4)
        self.assertAlmostEqual(a["protective_put"]["scenarios"][0]["pnl"],-901)
        self.assertAlmostEqual(a["put_spread"]["scenarios"][0]["pnl"],-8802)
        self.assertAlmostEqual(a["collar"]["scenarios"][-1]["pnl"],798)
        self.assertFalse(r["short_option_execution_supported"])
        self.assertFalse(r["broker_order_submission_enabled"])

    def test_credit_cannot_fund_premium(self):
        r=self.run_case(settled_cash=350,minimum_cash=10)
        self.assertEqual(r["arms"]["collar"]["status"],"INSUFFICIENT_SETTLED_CASH")
        self.assertEqual(r["arms"]["unhedged"]["status"],"TERMINAL_PAYOFF_DIAGNOSTIC")
        self.assertFalse(r["winner_selected"])

    def test_unsupported_contract_and_partial_coverage(self):
        with self.assertRaisesRegex(ValueError,"INCOMPATIBLE"):
            self.run_case(shares=150)
        self.terms.write_text(self.terms.read_text().replace("european,cash,0","american,cash,0"),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"INCOMPATIBLE"):
            self.run_case()

    def test_stale_spot_and_unsynchronized_quote(self):
        with self.assertRaisesRegex(ValueError,"STALE"):
            self.run_case(spot_available_at="2025-01-02T19:00Z")
        self.quotes.write_text(self.quotes.read_text().replace("C,2025-01-02T20:00Z","C,2025-01-02T19:59Z"),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"SYNCHRONIZED"):
            self.run_case()

    def test_displayed_liquidity_and_scenario_bounds(self):
        with self.assertRaisesRegex(ValueError,"LIQUIDITY"):
            self.run_case(shares=600)
        for spots in ([0,70,70,130],[100,120,130],[0,70,100]):
            with self.assertRaisesRegex(ValueError,"ORDERED"):
                self.run_case(terminal_spots=spots)
