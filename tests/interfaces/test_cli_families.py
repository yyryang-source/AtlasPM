"""Protect modular CLI handler and parser grouping."""

from __future__ import annotations

import argparse
import unittest

from atlaspm.cli import build_parser
from atlaspm.commands import broker_cli, quant_cli, selection_cli
from atlaspm.commands import (
    stock_data_cli, stock_history_cli, stock_models_cli, stock_rules_cli, stock_monthly_cli,
    multiasset_research_cli, portfolio_research_cli, quant_backtesting_cli, quant_models_cli,
    broker_accounts_cli, broker_risk_cli, broker_audits_cli, broker_paper_cli,
)

class FocusedCLIContractTests(unittest.TestCase):
    def test_submodules_partition_all_family_commands_exactly(self):
        for public, families in (
            (selection_cli, (stock_data_cli, stock_history_cli, stock_models_cli, stock_rules_cli, stock_monthly_cli)),
            (quant_cli, (multiasset_research_cli, portfolio_research_cli, quant_backtesting_cli, quant_models_cli)),
            (broker_cli, (broker_accounts_cli, broker_risk_cli, broker_audits_cli, broker_paper_cli)),
        ):
            with self.subTest(public=public.__name__):
                self.assertEqual(set().union(*(x.COMMANDS for x in families)), public.COMMANDS)
                self.assertEqual(sum(len(x.COMMANDS) for x in families), len(public.COMMANDS))

    def test_command_parser_contracts_preserved(self):
        parser = build_parser()
        parsers = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        self.assertEqual(len(parsers), 1)
        names = set(parsers[0].choices)
        self.assertEqual(names, selection_cli.COMMANDS | broker_cli.COMMANDS | quant_cli.COMMANDS | {'market-db', 'portfolio-engine', 'autonomous-alpha', 'architecture', 'lifecycle-review'})
        self.assertEqual(parser.parse_args(['market-db', 'status']).db_action, 'status')
        self.assertEqual(parser.parse_args(["allocate","--vol","SPY=0.2"]).command,"allocate")
        self.assertIn("stock-ai-rank-v2", parsers[0].choices)
        self.assertEqual(parser.parse_args(["stock-ai-train-v2","--prices","p.csv","--benchmark-prices","b.csv"]).top,10)
        self.assertEqual(parser.parse_args(["ibkr-readonly","--account","DU123"]).allow_local_gateway,False)
        self.assertEqual(parser.parse_args(["stock-split-reconcile","--prices","p.csv","--manifest","m.json"]).apply_confirmed,False)

if __name__ == "__main__":
    unittest.main()
