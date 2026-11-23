"""stock_rules_cli: offline AtlasPM command handlers; no live orders.

Focused CLI adapter, not the underlying quantitative strategy/model.
"""

from __future__ import annotations

import json
from pathlib import Path
from atlaspm.execution.ibkr_gateway import require_private_output
from atlaspm.strategies.equity.stock_screener import screen_stocks
from atlaspm.research.stock_walkforward import backtest_stock_screener
from atlaspm.strategies.equity.consensus_20_screener import run_20_signal_research

COMMANDS = frozenset(["stock-consensus-20","stock-backtest","stock-screen"])


def run(args) -> dict:
    if args.command == "stock-consensus-20":
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".json":
            raise ValueError("20-signal research output must be a private JSON file")
        inputs = {args.prices.expanduser().resolve()}
        if args.universe is not None:
            inputs.add(args.universe.expanduser().resolve())
        if destination in inputs:
            raise ValueError("Consensus report cannot overwrite input files")
        result = run_20_signal_research(
            args.prices, as_of=args.as_of,
            top=args.top, min_price=args.min_price,
            max_daily_ratio=args.max_daily_ratio,
            universe_path=args.universe,
            min_screenable=args.min_screenable,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "as_of": result["as_of"],
            "status": result["status"],
            "signal_count": result["n_signals"],
            "family_count": result["n_families"],
            "screenable_stocks": result["data_quality"]["eligible_price_only_names"],
            "excluded_counts": result["data_quality"]["excluded_counts"],
            "selected": result["selected"],
            "preview_not_trade_instructions": result["preview_not_trade_instructions"][:10],
            "review_reasons": result["review_reasons"],
            "written": str(destination.relative_to(Path.cwd().resolve())),
            "research_only": True,
            "live_execution_enabled": False,
            "automated_handoff_approved": False,
        }

    elif args.command == "stock-backtest":
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".json":
            raise ValueError("Stock backtest output must be private local_data .json")
        sources = {args.prices.expanduser().resolve(),
                   args.benchmark_prices.expanduser().resolve()}
        if destination in sources:
            raise ValueError("Stock backtest cannot overwrite its inputs")
        result = backtest_stock_screener(
            args.prices, args.benchmark_prices,
            start=args.start, end=args.end,
            benchmark_symbol=args.benchmark_symbol,
            top=args.top, rebalance_days=args.rebalance_days,
            fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
            max_volatility=args.max_volatility,
            max_drawdown=args.max_drawdown,
            require_uptrend=args.require_uptrend,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "written": "local_data/" + destination.relative_to(
                (Path.cwd().resolve() / "local_data").resolve()
            ).as_posix(),
            "requested_window": result["requested_window"],
            "actual_window": result["actual_window"],
            "universe_count": result["universe_count_excluding_benchmark"],
            "decision_count": result["decision_count"],
            "executed_rebalances": result["executed_rebalances"],
            "portfolio": result["portfolio"],
            "benchmark": result["benchmark"],
            "equal_weight_universe": result["equal_weight_universe"],
            "relative": result["relative"],
            "live_execution_enabled": False,
            "data_caution": "Surviving ticker universe; NOT survivorship-safe or certified alpha.",
        }

    elif args.command == "stock-screen":
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".json":
            raise ValueError("Stock screen output must be a private local_data .json file")
        inputs = {args.prices.expanduser().resolve()}
        if args.fundamentals:
            inputs.add(args.fundamentals.expanduser().resolve())
        if destination in inputs:
            raise ValueError("Refusing to overwrite stock screener input")
        result = screen_stocks(
            args.prices,
            args.as_of,
            fundamentals_path=args.fundamentals,
            top=args.top,
            max_price_age_days=args.max_price_age_days,
            max_fundamental_age_days=args.max_fundamental_age_days,
            max_annual_volatility=args.max_volatility,
            max_drawdown=args.max_drawdown,
            minimum_market_cap_usd=args.min_market_cap,
            require_uptrend=args.require_uptrend,
            max_per_sector=args.max_per_sector,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "written": "local_data/" + destination.relative_to(
                (Path.cwd().resolve() / "local_data").resolve()
            ).as_posix(),
            "as_of": result["as_of"],
            "mode": result["mode"],
            "universe_count": result["universe_count"],
            "eligible_count": result["eligible_count"],
            "excluded_count": len(result["excluded"]),
            "selected": [
                {"rank": row["rank"], "symbol": row["symbol"], "score": row["score"]}
                for row in result["selected"]
            ],
            "live_execution_enabled": False,
        }
    else:
        raise ValueError("Unsupported stock_rules_cli command")
    return report


def register_stock_consensus_20(sub) -> None:
    ensemble = sub.add_parser(
        "stock-consensus-20",
        help="Offline 20 original price signal screener in five factor families; research only",
    )
    ensemble.add_argument("--prices", type=Path, required=True)
    ensemble.add_argument("--universe", type=Path, default=None)
    ensemble.add_argument("--as-of", required=True)
    ensemble.add_argument("--top", type=int, default=10)
    ensemble.add_argument("--min-price", type=float, default=5.0)
    ensemble.add_argument("--max-daily-ratio", type=float, default=4.0)
    ensemble.add_argument("--min-screenable", type=int, default=100)
    ensemble.add_argument("--output", type=Path,
                          default=Path("local_data/consensus_20_research.json"))


def register_stock_backtest(sub) -> None:
    stock_backtest = sub.add_parser(
        "stock-backtest",
        help="Long-only 2000-2026 walk-forward stock ranking vs adjusted SPY, no orders",
    )
    stock_backtest.add_argument("--prices", type=Path, required=True)
    stock_backtest.add_argument("--benchmark-prices", type=Path, required=True)
    stock_backtest.add_argument("--benchmark-symbol", default="SPY")
    stock_backtest.add_argument("--start", default="2000-01-01")
    stock_backtest.add_argument("--end", default="2026-11-20")
    stock_backtest.add_argument("--top", type=int, default=10)
    stock_backtest.add_argument("--rebalance-days", type=int, default=21)
    stock_backtest.add_argument("--fee-bps", type=float, default=5.0)
    stock_backtest.add_argument("--slippage-bps", type=float, default=5.0)
    stock_backtest.add_argument("--max-volatility", type=float, default=0.80)
    stock_backtest.add_argument("--max-drawdown", type=float, default=0.65)
    stock_backtest.add_argument("--require-uptrend", action="store_true")
    stock_backtest.add_argument(
        "--output", type=Path,
        default=Path("local_data/stock_walkforward_2000_2026.json"),
    )


def register_stock_screen(sub) -> None:
    stock = sub.add_parser(
        "stock-screen",
        help="Offline point-in-time-capped multi-factor stock ranking; no orders",
    )
    stock.add_argument("--prices", type=Path, required=True,
                       help="Long CSV: date,symbol,adjusted_close")
    stock.add_argument("--as-of", required=True, help="Evaluation date YYYY-MM-DD")
    stock.add_argument("--fundamentals", type=Path, default=None,
                       help="Optional dated fundamental snapshot CSV")
    stock.add_argument("--top", type=int, default=10)
    stock.add_argument("--max-price-age-days", type=int, default=10)
    stock.add_argument("--max-fundamental-age-days", type=int, default=200)
    stock.add_argument("--max-volatility", type=float, default=0.80)
    stock.add_argument("--max-drawdown", type=float, default=0.65)
    stock.add_argument("--min-market-cap", type=float, default=0.0,
                       help="Minimum market_cap_usd; requires --fundamentals")
    stock.add_argument("--require-uptrend", action="store_true")
    stock.add_argument("--max-per-sector", type=int, default=None,
                       help="Max picks per sector; requires --fundamentals")
    stock.add_argument("--output", type=Path,
                       default=Path("local_data/stock_screen.json"))

