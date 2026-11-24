"""Stable public selection_cli interface: delegates to small command families.

All command flags, output schemas, error behavior and permissions remain unchanged.
"""

from __future__ import annotations

from atlaspm.commands import stock_data_cli, stock_history_cli, stock_models_cli, stock_rules_cli, stock_monthly_cli

COMMANDS = frozenset({
    "stock-alpha-gate",
    "stock-alpha-check",
    "stock-ai-monthly",
    "stock-consensus-20",
    "stock-ai-train-v2",
    "stock-ai-diagnose-momentum",
    "stock-split-reconcile",
    "stock-ai-rank-v2",
    "stock-ai-train",
    "stock-ai-rank",
    "stock-universe",
    "stock-market-audit",
    "stock-sync-market",
    "stock-backtest",
    "stock-screen",
}) | stock_history_cli.COMMANDS


def run(args) -> dict:
    if args.command in stock_data_cli.COMMANDS:
        return stock_data_cli.run(args)
    elif args.command in stock_models_cli.COMMANDS:
        return stock_models_cli.run(args)
    elif args.command in stock_rules_cli.COMMANDS:
        return stock_rules_cli.run(args)
    elif args.command in stock_monthly_cli.COMMANDS:
        return stock_monthly_cli.run(args)
    elif args.command in stock_history_cli.COMMANDS:
        return stock_history_cli.run(args)
    raise ValueError("Unsupported selection_cli command")


def register_parsers(sub) -> None:
    stock_monthly_cli.register_stock_alpha_gate(sub)
    stock_monthly_cli.register_stock_alpha_check(sub)
    stock_monthly_cli.register_stock_ai_monthly(sub)
    stock_rules_cli.register_stock_consensus_20(sub)
    stock_models_cli.register_stock_ai_train_v2(sub)
    stock_models_cli.register_stock_ai_diagnose_momentum(sub)
    stock_data_cli.register_stock_split_reconcile(sub)
    stock_models_cli.register_stock_ai_rank_v2(sub)
    stock_models_cli.register_stock_ai_train(sub)
    stock_models_cli.register_stock_ai_rank(sub)
    stock_data_cli.register_stock_universe(sub)
    stock_data_cli.register_stock_market_audit(sub)
    stock_data_cli.register_stock_sync_market(sub)
    stock_rules_cli.register_stock_backtest(sub)
    stock_rules_cli.register_stock_screen(sub)
    stock_history_cli.register_parsers(sub)


