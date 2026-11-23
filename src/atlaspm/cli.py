"""Public AtlasPM CLI. Stable PowerShell commands delegate to focused modules."""

from __future__ import annotations

import argparse
import json
import sys

from atlaspm.commands import broker, quant, selection
# Backward-compatible public injection point used by offline data CLI tests.
from atlaspm.quant.historical import download_yahoo_adjusted_prices


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlaspm", description="AtlasPM: local portfolio research toolkit"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    # Preserve original top-level command ordering for terminal users.
    quant.register_core_parsers(sub)
    broker.register_parsers(sub)
    quant.register_model_parsers(sub)
    selection.register_parsers(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in selection.COMMANDS:
            report = selection.run(args)
        elif args.command in broker.COMMANDS:
            report = broker.run(args)
        elif args.command in quant.COMMANDS:
            report = quant.run(args, price_downloader=download_yahoo_adjusted_prices)
        else:
            raise ValueError("Unsupported AtlasPM command")
        print(json.dumps(report, indent=2, sort_keys=True))
        if args.command == "stock-sync-market" and report.get("stop_reason") == "USER_INTERRUPTED":
            # Preserve partial cache before the Windows runner exits.
            return 130
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"AtlasPM error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
