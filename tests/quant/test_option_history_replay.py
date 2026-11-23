"""Offline contract data, execution and settlement regressions."""

import contextlib
import csv
from datetime import timedelta
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from atlaspm.cli import main
from atlaspm.quant.option_history import OptionHistory, option_timestamp
from atlaspm.quant.option_replay import replay_long_options


class OptionReplayTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.contracts = self.root / "contracts.csv"
        self.quotes = self.root / "quotes.csv"
        self.settlements = self.root / "settlements.csv"
        self._csv(self.contracts, [
            ["contract_id", "underlying", "right", "strike", "last_trade_at", "expiration_at",
             "multiplier", "exercise_style", "settlement_type", "exercise_threshold_usd"],
            ["P", "INDEX", "put", 100, "2024-01-12T20:59Z", "2024-01-12T21:00Z",
             100, "european", "cash", 1],
            ["PP", "STOCK", "put", 100, "2024-01-12T20:59Z", "2024-01-12T21:00Z",
             100, "american", "physical", 1],
            ["PC", "STOCK", "call", 90, "2024-01-12T20:59Z", "2024-01-12T21:00Z",
             100, "american", "physical", 1],
            ["OTM", "INDEX", "put", 90, "2024-01-12T20:59Z", "2024-01-12T21:00Z",
             100, "european", "cash", 1],
        ])
        self._csv(self.quotes, [
            ["contract_id", "quote_at", "bid", "ask", "bid_size", "ask_size"],
            ["P", "2024-01-08T20:58Z", 2, 2.2, 5, 2],
            ["P", "2024-01-08T21:01Z", 8, 8.2, 50, 50],
            ["P", "2024-01-09T20:58Z", 3, 3.4, 3, 5],
            ["P", "2024-01-10T14:30Z", 3, 3.4, 3, 5],
            ["PP", "2024-01-08T20:58Z", 2, 2.2, 5, 5],
            ["PC", "2024-01-08T20:58Z", 4, 4.4, 5, 5],
            ["OTM", "2024-01-08T20:58Z", .4, .5, 5, 5],
        ])
        self._csv(self.settlements, [
            ["contract_id", "reference_at", "available_at", "cash_at", "settlement_value"],
            *[[key, "2024-01-12T21:00Z", "2024-01-12T21:02Z", "2024-01-16T14:30Z", 95]
              for key in ("P", "PP", "PC", "OTM")],
        ])

    @staticmethod
    def _csv(path, rows):
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)

    def _replay(self, plan):
        return replay_long_options(OptionHistory(self.contracts, self.quotes, self.settlements), plan)

    def test_lean_minute_close_becomes_available_after_bar_ends(self):
        archive = self.root / "quotes.zip"
        with ZipFile(archive, "w") as handle:
            handle.writestr("20240108_stock_minute_quote_american_put_1000000_20240112.csv",
                            "34200000,17000,22000,15000,20000,9,19000,25000,18000,22000,7\n")
        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["option-import-lean-quotes", "--zip", str(archive),
                                       "--contracts", str(self.contracts)]), 0)
            history = OptionHistory(self.contracts, Path("local_data/option_quotes.csv"), self.settlements)
        finally:
            os.chdir(cwd)
        with self.assertRaisesRegex(ValueError, "No fresh"):
            history.quote_at("PP", option_timestamp("2024-01-08T14:30:59Z"), timedelta(minutes=5))
        available = option_timestamp("2024-01-08T14:31:00Z")
        quote = history.quote_at("PP", available, timedelta(minutes=5))
        self.assertEqual(quote.at, available)
        self.assertEqual((quote.bid, quote.ask, quote.bid_size, quote.ask_size), (2, 2.2, 9, 7))

    def test_cli_buy_ask_sell_bid_and_delayed_cash_settlement(self):
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({
            "initial_cash_usd": 2000, "fee_per_contract_usd": 1,
            "orders": [{"at": "2024-01-08T21:00Z", "contract_id": "P", "side": "buy", "contracts": 2},
                       {"at": "2024-01-09T21:00Z", "contract_id": "P", "side": "sell", "contracts": 1,
                        "cash_at": "2024-01-10T14:30Z"}],
            "valuation_times": ["2024-01-08T21:00Z", "2024-01-09T21:00Z", "2024-01-10T14:30Z",
                                "2024-01-12T21:01Z", "2024-01-12T21:02Z", "2024-01-16T14:30Z"],
        }), encoding="utf-8")
        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["option-history-replay", "--contracts", str(self.contracts), "--quotes",
                                       str(self.quotes), "--settlements", str(self.settlements), "--plan", str(plan)]), 0)
            report = json.loads(Path(json.loads(out.getvalue())["written"]).read_text(encoding="utf-8"))
        finally:
            os.chdir(cwd)
        self.assertEqual([row["fill_price"] for row in report["fills"]], [2.2, 3])
        self.assertAlmostEqual(report["spread_cost_vs_mid_usd"], 40)
        self.assertEqual(report["execution_fees_usd"], 3)
        marks = report["valuations"]
        self.assertAlmostEqual(marks[1]["cash_available_usd"], 1557)
        self.assertEqual(marks[1]["cash_receivable_usd"], 300)
        self.assertAlmostEqual(marks[2]["cash_available_usd"], 1857)
        self.assertIsNone(marks[3]["option_bid_value_usd"])
        self.assertEqual(marks[4]["cash_receivable_usd"], 500)
        self.assertEqual(marks[4]["option_positions"], {})
        self.assertAlmostEqual(marks[5]["cash_available_usd"], 2357)

    def test_physical_deliveries_and_out_of_money_expiry(self):
        report = self._replay({
            "initial_cash_usd": 12000, "initial_shares": {"STOCK": 100},
            "orders": [{"at": "2024-01-08T21:00Z", "contract_id": key, "side": "buy", "contracts": 1}
                       for key in ("PP", "PC", "OTM")],
            "valuation_times": ["2024-01-12T21:02Z", "2024-01-16T14:30Z"],
        })
        before, after = report["valuations"]
        self.assertAlmostEqual(before["cash_available_usd"], 2290)
        self.assertEqual(before["cash_receivable_usd"], 10000)
        self.assertEqual(before["shares_available"]["STOCK"], 0)
        self.assertEqual(before["shares_receivable"], {"STOCK": 100})
        self.assertEqual(after["shares_available"]["STOCK"], 100)
        self.assertAlmostEqual(after["cash_available_usd"], 12290)
        self.assertFalse(next(row for row in report["expirations"] if row["contract_id"] == "OTM")["exercised"])

    def test_rejects_future_stale_and_already_consumed_quotes(self):
        for at, error in [("2024-01-08T20:57Z", "No fresh"), ("2024-01-08T21:10Z", "No fresh")]:
            with self.subTest(at=at), self.assertRaisesRegex(ValueError, error):
                self._replay({"initial_cash_usd": 2000, "valuation_times": [at], "orders": [
                    {"contract_id": "P", "at": at, "side": "buy", "contracts": 1},
                ]})
        with self.assertRaisesRegex(ValueError, "displayed option liquidity"):
            self._replay({"initial_cash_usd": 2000, "valuation_times": ["2024-01-08T21:00Z"], "orders": [
                {"contract_id": "P", "at": "2024-01-08T20:59Z", "side": "buy", "contracts": 2},
                {"contract_id": "P", "at": "2024-01-08T21:00Z", "side": "buy", "contracts": 1},
            ]})

    def test_open_expiry_requires_an_explicit_reference(self):
        self.settlements.write_text("contract_id,reference_at,available_at,cash_at,settlement_value\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Missing official expiration settlement"):
            self._replay({"initial_cash_usd": 2000, "valuation_times": ["2024-01-12T21:01Z"], "orders": [
                {"contract_id": "P", "at": "2024-01-08T20:59Z", "side": "buy", "contracts": 1},
            ]})

    def test_rejects_settlement_reference_before_last_trade_date(self):
        self._csv(self.settlements, [
            ["contract_id", "reference_at", "available_at", "cash_at", "settlement_value"],
            ["P", "2024-01-11T21:00Z", "2024-01-12T21:02Z", "2024-01-16T14:30Z", 95],
        ])
        with self.assertRaisesRegex(ValueError, "Invalid settlement value or chronology for P"):
            OptionHistory(self.contracts, self.quotes, self.settlements)
