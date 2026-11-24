"""Stable CLI command names and option contracts after module separation."""

from __future__ import annotations

import argparse
import unittest

from atlaspm.cli import build_parser
from atlaspm.commands import broker_cli, quant_cli, selection_cli


class ModularCLIContractTests(unittest.TestCase):
    def test_all_45_commands_are_unique_and_registered(self):
        groups = (quant_cli.COMMANDS, broker_cli.COMMANDS, selection_cli.COMMANDS)
        self.assertEqual([len(g) for g in groups], [14, 16, 15])
        self.assertEqual(len(set.union(*(set(g) for g in groups))), 45)
        self.assertEqual(sum(map(len, groups)), 45)
        parser = build_parser()
        parsers = [action for action in parser._actions
                   if isinstance(action, argparse._SubParsersAction)]
        self.assertEqual(len(parsers), 1)
        self.assertEqual(set(parsers[0].choices), set.union(*(set(g) for g in groups)) | {'market-db', 'portfolio-engine'})

    def test_command_modules_do_not_shadow_algorithm_packages(self):
        """CLI adapters are distinct from the actual quant/selection implementations."""
        from pathlib import Path
        import atlaspm

        package = Path(atlaspm.__file__).resolve().parent
        self.assertTrue((package / "selection" / "ai_stock_ranker_v2.py").is_file())
        self.assertTrue((package / "quant" / "portfolio.py").is_file())
        self.assertTrue((package / "commands" / "selection_cli.py").is_file())
        self.assertTrue((package / "commands" / "quant_cli.py").is_file())
        self.assertTrue((package / "commands" / "broker_cli.py").is_file())
        for old_name in ("selection.py", "quant.py", "broker.py"):
            self.assertFalse((package / "commands" / old_name).exists())

    def test_existing_python_and_windows_cli_shapes(self):
        parser = build_parser()
        for command in ("stock-ai-train-v2", "ibkr-margin-monitor",
                        "atlas-native-backtest", "analyze", "stock-split-reconcile"):
            with self.subTest(command=command):
                self.assertIn(command, parser.format_help())
        options = parser.parse_args(["stock-ai-train-v2", "--prices", "p.csv",
                                     "--benchmark-prices", "spy.csv"])
        self.assertEqual(options.command, "stock-ai-train-v2")
        self.assertEqual(options.top, 10)
        self.assertEqual(options.horizon_sessions, 21)
        audit = parser.parse_args(["stock-split-reconcile", "--prices", "x.csv",
                                   "--manifest", "events.json"])
        self.assertFalse(audit.apply_confirmed)
        self.assertIsNone(audit.output)
        gateway = parser.parse_args(["ibkr-readonly", "--account", "DU123456"])
        self.assertFalse(gateway.allow_local_gateway)
        self.assertIsNone(gateway.fixture)


if __name__ == "__main__":
    unittest.main()
