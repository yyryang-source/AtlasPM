"""Stable public quant_cli interface: delegates to small command families.

All command flags, output schemas, error behavior and permissions remain unchanged.
"""

from __future__ import annotations

from atlaspm.commands import portfolio_research_cli, quant_backtesting_cli, quant_models_cli

COMMANDS = frozenset({
    "analyze",
    "allocate",
    "optimize",
    "download-prices",
    "estimate",
    "backtest",
    "audit",
    "benchmark-spy",
    "risk-check",
    "backtest-risk",
    "ml-rebalance-backtest",
    "ml-model-tournament",
    "atlas-native-backtest",
    "atlas-native-audit",
})


def run(args, *, price_downloader) -> dict:
    if args.command in portfolio_research_cli.COMMANDS:
        return portfolio_research_cli.run(args, price_downloader=price_downloader)
    elif args.command in quant_backtesting_cli.COMMANDS:
        return quant_backtesting_cli.run(args)
    elif args.command in quant_models_cli.COMMANDS:
        return quant_models_cli.run(args)
    raise ValueError("Unsupported quant_cli command")


def register_core_parsers(sub) -> None:
    portfolio_research_cli.register_analyze(sub)
    portfolio_research_cli.register_allocate(sub)
    portfolio_research_cli.register_optimize(sub)
    portfolio_research_cli.register_download_prices(sub)
    portfolio_research_cli.register_estimate(sub)
    quant_backtesting_cli.register_backtest(sub)
    quant_backtesting_cli.register_audit(sub)
    quant_backtesting_cli.register_benchmark_spy(sub)
    quant_backtesting_cli.register_risk_check(sub)
    quant_backtesting_cli.register_backtest_risk(sub)


def register_model_parsers(sub) -> None:
    quant_models_cli.register_ml_rebalance_backtest(sub)
    quant_models_cli.register_ml_model_tournament(sub)
    quant_models_cli.register_atlas_native_backtest(sub)
    quant_models_cli.register_atlas_native_audit(sub)


