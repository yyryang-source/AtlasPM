"""portfolio_research_cli: offline AtlasPM command handlers; no live orders.

Focused CLI adapter, not the underlying quantitative strategy/model.
"""

from __future__ import annotations

import json
from pathlib import Path
from atlaspm.engine.optimizer import inverse_volatility_weights
from atlaspm.data.historical import estimate_from_prices
from atlaspm.engine.mean_variance import optimize_mean_variance
from atlaspm.engine.portfolio import analyze_accounts, parse_accounts

COMMANDS = frozenset(["analyze","allocate","optimize","download-prices","estimate"])


def run(args, *, price_downloader) -> dict:
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
        report = price_downloader(
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

    elif args.command == "allocate":
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
        raise ValueError("Unsupported portfolio_research_cli command")
    return report


def register_analyze(sub) -> None:
    analyze = sub.add_parser("analyze", help="Risk report from JSON input")
    analyze.add_argument("--file", type=Path, required=True)
    analyze.add_argument(
        "--declines", type=float, nargs="+", default=[0.10, 0.20, 0.30],
        help="Uniform decline fractions (e.g. 0.10 means down 10%%)",
    )
    analyze.add_argument(
        "--apr", type=float, default=0.065, help="Annual margin borrowing rate"
    )


def register_allocate(sub) -> None:
    allocate = sub.add_parser(
        "allocate", help="Inverse-volatility capped allocation baseline"
    )
    allocate.add_argument(
        "--vol", nargs="+", required=True,
        help="Annualized volatility items such as QQQ=0.25 NVDA=0.50",
    )
    allocate.add_argument("--max-weight", type=float, default=0.60)


def register_optimize(sub) -> None:
    optim = sub.add_parser("optimize", help="Constrained mean-variance scenario")
    optim.add_argument("--file", type=Path, required=True)
    optim.add_argument("--risk-aversion", type=float, default=4.0)
    optim.add_argument("--max-weight", type=float, default=0.60)
    optim.add_argument("--leverage", type=float, default=1.0)
    optim.add_argument("--apr", type=float, default=0.065)


def register_download_prices(sub) -> None:
    prices = sub.add_parser("download-prices", help="Download adjusted daily prices to a local CSV")
    prices.add_argument("--symbols", nargs="+", required=True)
    prices.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    prices.add_argument("--end", required=True, help="YYYY-MM-DD, exclusive")
    prices.add_argument("--output", type=Path, default=Path("local_data/prices.csv"))
    prices.add_argument("--overwrite", action="store_true")


def register_estimate(sub) -> None:
    estimate = sub.add_parser("estimate", help="Estimate annual returns and covariance from local CSV")
    estimate.add_argument("--file", type=Path, required=True)
    estimate.add_argument("--output", type=Path, default=Path("local_data/estimates.json"))
    estimate.add_argument("--min-observations", type=int, default=60)

