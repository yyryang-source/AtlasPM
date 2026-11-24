"""CLI commands for historical stock membership, fundamentals and strategy comparison."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from atlaspm.execution.ibkr_gateway import require_private_output
from atlaspm.data.historical_universe import DEFAULT_MAX_UNIVERSE_AGE_DAYS
from atlaspm.data.historical_stock_imports import import_cash_exits, import_membership_spans
from atlaspm.data.sec_fundamental_history import import_sec_factors
from atlaspm.data.stock_fundamentals_import import import_stock_fundamentals
from atlaspm.research.stock_comparison import compare_stock_selection
from atlaspm.research.stock_history_audit import audit_stock_history

COMMANDS = frozenset({
    'stock-history-audit',
    'stock-compare',
    'stock-import-membership-spans',
    'stock-import-cash-exits',
    'stock-import-sec-factors',
    'stock-import-fundamentals',
})


def run(args) -> dict:
    if args.command == 'stock-history-audit':
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".json":
            raise ValueError("Stock history audit output must be a private local_data .json")
        sources = {
            path.expanduser().resolve()
            for path in (args.prices, args.benchmark_prices, args.universe_history)
        }
        if destination in sources:
            raise ValueError("Stock history audit cannot overwrite its inputs")
        details = audit_stock_history(
            args.prices, args.benchmark_prices, args.universe_history,
            start=args.start, end=args.end, benchmark_symbol=args.benchmark_symbol,
            rebalance_days=args.rebalance_days,
            max_universe_age_days=args.max_universe_age_days,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(details, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8",
        )
        report = {key: value for key, value in details.items() if key != "decisions"}
        report["written"] = destination.relative_to(Path.cwd().resolve()).as_posix()
    elif args.command == 'stock-compare':
        destination = require_private_output(args.output)
        sources = {path.expanduser().resolve() for path in (
            args.prices, args.benchmark_prices, args.universe_history, args.fundamentals, args.delistings,
        ) if path is not None}
        if destination.suffix.lower() != ".json" or destination in sources:
            raise ValueError("Stock comparison requires a private .json output distinct from inputs")
        result = compare_stock_selection(
            args.prices, args.benchmark_prices, args.universe_history, args.fundamentals,
            train_start=args.train_start, train_end=args.train_end, validation_end=args.validation_end,
            test_start=args.test_start, test_end=args.test_end, benchmark_symbol=args.benchmark_symbol,
            delistings_path=args.delistings, top=args.top, rebalance_days=args.rebalance_days,
            fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
            max_universe_age_days=args.max_universe_age_days,
            max_fundamental_age_days=args.max_fundamental_age_days,
            num_boost_round=args.num_boost_round, min_train_rows=args.min_train_rows,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8",
        )
        report = {
            "written": destination.relative_to(Path.cwd().resolve()).as_posix(),
            "windows": result["windows"],
            "strategies": {name: {key: values[key] for key in ("total_return", "max_drawdown", "sum_turnover_ratios")}
                           for name, values in result["strategies"].items()},
            "live_execution_enabled": False,
        }
    elif args.command == 'stock-import-membership-spans':
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".csv" or destination.exists():
            raise ValueError("Membership import requires a new private .csv output")
        report = import_membership_spans(args.spans, args.benchmark_prices, args.start, args.end, destination,
                                         benchmark_symbol=args.benchmark_symbol)
        report["written"] = destination.relative_to(Path.cwd().resolve()).as_posix()
    elif args.command == 'stock-import-cash-exits':
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".csv" or destination.exists():
            raise ValueError("Cash exit import requires a new private .csv output")
        report = import_cash_exits(args.events, args.raw_prices, args.adjusted_prices, destination)
        report["written"] = destination.relative_to(Path.cwd().resolve()).as_posix()
    elif args.command == 'stock-import-sec-factors':
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".csv" or destination.exists():
            raise ValueError("SEC factor import requires a new private .csv output")
        report = import_sec_factors(args.securities, args.facts_directory, args.raw_prices, args.benchmark_prices,
                                    args.start, args.end, destination, benchmark_symbol=args.benchmark_symbol)
        report["written"] = destination.relative_to(Path.cwd().resolve()).as_posix()
    elif args.command == 'stock-import-fundamentals':
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".csv":
            raise ValueError("Fundamentals import requires a private .csv output")
        if destination.exists():
            raise ValueError("Fundamentals import needs a new output file")
        report = import_stock_fundamentals(
            args.filings, args.market_caps, args.benchmark_prices, destination,
            benchmark_symbol=args.benchmark_symbol,
        )
        report["written"] = destination.relative_to(Path.cwd().resolve()).as_posix()
    else:
        raise ValueError("Unsupported research command")
    return report


def register_parsers(sub) -> None:
    history_audit = sub.add_parser(
        "stock-history-audit",
        help="Audit dated stock membership and close-price coverage on backtest decision dates",
    )
    history_audit.add_argument("--prices", type=Path, required=True)
    history_audit.add_argument("--benchmark-prices", type=Path, required=True)
    history_audit.add_argument("--universe-history", type=Path, required=True)
    history_audit.add_argument("--start", required=True)
    history_audit.add_argument("--end", required=True)
    history_audit.add_argument("--benchmark-symbol", default="SPY")
    history_audit.add_argument("--rebalance-days", type=int, default=21)
    history_audit.add_argument("--max-universe-age-days", type=int,
                               default=DEFAULT_MAX_UNIVERSE_AGE_DAYS)
    history_audit.add_argument("--output", type=Path,
                               default=Path("local_data/stock_history_audit.json"))

    comparison = sub.add_parser("stock-compare", help="Compare four stock strategies on a fixed test window")
    for flag in ("prices", "benchmark-prices", "universe-history", "fundamentals"):
        comparison.add_argument(f"--{flag}", type=Path, required=True)
    for flag in ("train-start", "train-end", "validation-end", "test-start", "test-end"):
        comparison.add_argument(f"--{flag}", required=True)
    comparison.add_argument("--benchmark-symbol", default="SPY")
    comparison.add_argument("--delistings", type=Path)
    comparison.add_argument("--top", type=int, default=10)
    comparison.add_argument("--rebalance-days", type=int, default=21)
    comparison.add_argument("--fee-bps", type=float, default=5.0)
    comparison.add_argument("--slippage-bps", type=float, default=5.0)
    comparison.add_argument("--max-universe-age-days", type=int, default=DEFAULT_MAX_UNIVERSE_AGE_DAYS)
    comparison.add_argument("--max-fundamental-age-days", type=int, default=200)
    comparison.add_argument("--num-boost-round", type=int, default=300)
    comparison.add_argument("--min-train-rows", type=int, default=300)
    comparison.add_argument("--output", type=Path, default=Path("local_data/stock_comparison.json"))

    membership = sub.add_parser("stock-import-membership-spans", help="Expand dated universe membership to benchmark sessions")
    membership.add_argument("--spans", type=Path, required=True)
    membership.add_argument("--benchmark-prices", type=Path, required=True)
    membership.add_argument("--benchmark-symbol", default="SPY")
    membership.add_argument("--start", type=date.fromisoformat, required=True)
    membership.add_argument("--end", type=date.fromisoformat, required=True)
    membership.add_argument("--output", type=Path, default=Path("local_data/historical_membership.csv"))

    cash_exits = sub.add_parser("stock-import-cash-exits", help="Translate source-backed cash exit amounts into terminal returns")
    for option in ("events", "raw-prices", "adjusted-prices"):
        cash_exits.add_argument(f"--{option}", type=Path, required=True)
    cash_exits.add_argument("--output", type=Path, default=Path("local_data/stock_cash_exits.csv"))

    sec_factors = sub.add_parser("stock-import-sec-factors", help="Build publication-dated factors from SEC Company Facts")
    for option in ("securities", "facts-directory", "raw-prices", "benchmark-prices"):
        sec_factors.add_argument(f"--{option}", type=Path, required=True)
    sec_factors.add_argument("--benchmark-symbol", default="SPY")
    sec_factors.add_argument("--start", type=date.fromisoformat, required=True)
    sec_factors.add_argument("--end", type=date.fromisoformat, required=True)
    sec_factors.add_argument("--output", type=Path, default=Path("local_data/sec_stock_factors.csv"))

    fundamentals_import = sub.add_parser(
        "stock-import-fundamentals", help="Convert annual filings and dated valuations to screener CSV",
    )
    fundamentals_import.add_argument("--filings", type=Path, required=True)
    fundamentals_import.add_argument("--market-caps", type=Path, required=True)
    fundamentals_import.add_argument("--benchmark-prices", type=Path, required=True)
    fundamentals_import.add_argument("--benchmark-symbol", default="SPY")
    fundamentals_import.add_argument("--output", type=Path, default=Path("local_data/published_fundamentals.csv"))
