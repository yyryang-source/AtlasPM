"""Command-line entrypoint for reproducible Windows/local experiments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from atlaspm.optimizer import inverse_volatility_weights
from atlaspm.historical import download_yahoo_adjusted_prices, estimate_from_prices
from atlaspm.mean_variance import optimize_mean_variance
from atlaspm.portfolio import analyze_accounts, parse_accounts
from atlaspm.backtest import backtest_walk_forward


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlaspm", description="AtlasPM: local portfolio research toolkit"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="Risk report from JSON input")
    analyze.add_argument("--file", type=Path, required=True)
    analyze.add_argument(
        "--declines", type=float, nargs="+", default=[0.10, 0.20, 0.30],
        help="Uniform decline fractions (e.g. 0.10 means down 10%%)",
    )
    analyze.add_argument(
        "--apr", type=float, default=0.065, help="Annual margin borrowing rate"
    )

    allocate = sub.add_parser(
        "allocate", help="Inverse-volatility capped allocation baseline"
    )
    allocate.add_argument(
        "--vol", nargs="+", required=True,
        help="Annualized volatility items such as QQQ=0.25 NVDA=0.50",
    )
    allocate.add_argument("--max-weight", type=float, default=0.60)
    optim = sub.add_parser("optimize", help="Constrained mean-variance scenario")
    optim.add_argument("--file", type=Path, required=True)
    optim.add_argument("--risk-aversion", type=float, default=4.0)
    optim.add_argument("--max-weight", type=float, default=0.60)
    optim.add_argument("--leverage", type=float, default=1.0)
    optim.add_argument("--apr", type=float, default=0.065)
    prices = sub.add_parser("download-prices", help="Download adjusted daily prices to a local CSV")
    prices.add_argument("--symbols", nargs="+", required=True)
    prices.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    prices.add_argument("--end", required=True, help="YYYY-MM-DD, exclusive")
    prices.add_argument("--output", type=Path, default=Path("local_data/prices.csv"))
    prices.add_argument("--overwrite", action="store_true")

    estimate = sub.add_parser("estimate", help="Estimate annual returns and covariance from local CSV")
    estimate.add_argument("--file", type=Path, required=True)
    estimate.add_argument("--output", type=Path, default=Path("local_data/estimates.json"))
    estimate.add_argument("--min-observations", type=int, default=60)

    backtest = sub.add_parser("backtest", help="Walk-forward, past-only portfolio simulation")
    backtest.add_argument("--file", type=Path, required=True)
    backtest.add_argument("--output", type=Path, default=Path("local_data/backtest.json"))
    backtest.add_argument("--lookback-days", type=int, default=756)
    backtest.add_argument("--rebalance-days", type=int, default=63)
    backtest.add_argument("--risk-aversion", type=float, default=4.0)
    backtest.add_argument("--max-weight", type=float, default=0.60)
    backtest.add_argument("--leverage", type=float, default=1.0)
    backtest.add_argument("--apr", type=float, default=0.065)
    backtest.add_argument("--fee-bps", type=float, default=5.0)
    backtest.add_argument("--maintenance", type=float, default=0.30)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            payload = json.loads(args.file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Input file must contain an object")
            report = analyze_accounts(
                parse_accounts(payload),
                declines=args.declines,
                annual_margin_rate=args.apr,
            )
        elif args.command == "download-prices":
            report = download_yahoo_adjusted_prices(
                args.symbols, args.start, args.end, args.output, args.overwrite
            )
        elif args.command == "estimate":
            report = estimate_from_prices(args.file, min_observations=args.min_observations)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            report = {
                "written": str(args.output),
                "metadata": report["metadata"],
                "next_step": f"atlaspm optimize --file {args.output}",
            }
        elif args.command == "backtest":
            report = backtest_walk_forward(
                args.file,
                lookback_days=args.lookback_days,
                rebalance_days=args.rebalance_days,
                risk_aversion=args.risk_aversion,
                max_weight=args.max_weight,
                leverage=args.leverage,
                annual_financing_rate=args.apr,
                fee_bps=args.fee_bps,
                maintenance_fraction=args.maintenance,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "data": report["data"],
                "parameters": report["parameters"],
                "strategy": report["strategy"],
                "benchmark": report["benchmark"],
                "warnings": report["warnings"],
            }
        elif args.command == "optimize":
            assumptions = json.loads(args.file.read_text(encoding="utf-8"))
            if not isinstance(assumptions, dict):
                raise ValueError("Input file must be an object")
            report = optimize_mean_variance(
                assumptions["expected_returns"],
                assumptions["covariance"],
                risk_aversion=args.risk_aversion,
                max_weight=args.max_weight,
                leverage=args.leverage,
                annual_financing_rate=args.apr,
            )
        else:
            vols: dict[str, float] = {}
            for item in args.vol:
                symbol, sep, raw = item.partition("=")
                if not sep or not symbol.strip() or symbol in vols:
                    raise ValueError(f"Invalid or duplicate --vol entry: {item}")
                vols[symbol.strip()] = float(raw)
            report = {
                "method": "inverse_volatility_capped_baseline",
                "weights": inverse_volatility_weights(vols, args.max_weight),
                "disclaimer": "Heuristic baseline, not a trained AI or a trade signal.",
            }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"AtlasPM error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
