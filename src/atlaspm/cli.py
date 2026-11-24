"""Public AtlasPM CLI. Stable PowerShell commands delegate to focused modules."""

from __future__ import annotations

import argparse
import json
import sys

from atlaspm.commands import broker_cli, quant_cli, selection_cli, portfolio_engine_cli
from atlaspm.data import market_db
# Backward-compatible public injection point used by offline data CLI tests.
from atlaspm.data.historical import download_yahoo_adjusted_prices


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlaspm", description="AtlasPM: local portfolio research toolkit"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    # Preserve original top-level command ordering for terminal users.
    quant_cli.register_core_parsers(sub)
    broker_cli.register_parsers(sub)
    quant_cli.register_model_parsers(sub)
    selection_cli.register_parsers(sub)
    market_db.register_parser(sub)
    portfolio_engine_cli.register_parser(sub)
    alpha = sub.add_parser("autonomous-alpha", help="Synthetic autonomous alpha research")
    alpha.add_argument("--file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "autonomous-alpha":
            from pathlib import Path
            from atlaspm.research.autonomous_alpha import research, simulate
            request = json.loads(Path(args.file).read_text(encoding="utf-8"))
            report = simulate(**request) if "portfolio" in request else research(**request)
        elif args.command == "portfolio-engine":
            report = portfolio_engine_cli.run(args)
        elif args.command == "market-db":
            report = market_db.run(args)
        elif args.command in selection_cli.COMMANDS:
            report = selection_cli.run(args)
        elif args.command in broker_cli.COMMANDS:
            report = broker_cli.run(args)
        elif args.command in quant_cli.COMMANDS:
            report = quant_cli.run(args, price_downloader=download_yahoo_adjusted_prices)
        else:
            raise ValueError("Unsupported AtlasPM command")
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        print()
        if args.command == "stock-sync-market" and report.get("stop_reason") == "USER_INTERRUPTED":
            # Preserve partial cache before the Windows runner exits.
            return 130
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"AtlasPM error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
