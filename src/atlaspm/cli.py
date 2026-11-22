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
from atlaspm.audit import audit_backtests
from atlaspm.benchmark import compare_spy
from atlaspm.risk_budget import check_risk_budget
from atlaspm.risk_backtest import backtest_risk_budget


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
    backtest.add_argument("--margin-policy", choices=["none", "next_close_deleverage_to_1x"], default="none")

    audit = sub.add_parser("audit", help="Compare saved cash and margin backtests")
    audit.add_argument("--cash", type=Path, required=True)
    audit.add_argument("--margin", type=Path, required=True)
    audit.add_argument("--output", type=Path, default=Path("local_data/risk_audit.json"))

    benchmark = sub.add_parser("benchmark-spy", help="Exact-date independent SPY comparison")
    benchmark.add_argument("--cash", type=Path, required=True)
    benchmark.add_argument("--margin", type=Path, required=True)
    benchmark.add_argument("--spy", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, default=Path("local_data/spy_comparison.json"))

    risk = sub.add_parser("risk-check", help="Read-only pre-trade research risk budget")
    risk.add_argument("--file", type=Path, required=True, help="Historical estimate JSON")
    risk.add_argument("--leverage", type=float, default=2.0)
    risk.add_argument("--risk-aversion", type=float, default=4.0)
    risk.add_argument("--max-weight", type=float, default=0.60, help="Optimizer concentration cap")
    risk.add_argument("--max-leverage", type=float, default=2.0)
    risk.add_argument("--max-name-weight", type=float, default=0.60, help="Risk budget concentration cap")
    risk.add_argument("--max-volatility", type=float, default=0.30)
    risk.add_argument("--stress-decline", type=float, default=0.30)
    risk.add_argument("--max-stress-loss", type=float, default=0.40)
    risk.add_argument("--maintenance", type=float, default=0.30)
    risk.add_argument("--min-buffer", type=float, default=0.05)
    risk.add_argument("--apr", type=float, default=0.065)
    risk.add_argument("--max-financing-cost", type=float, default=0.08)
    risk.add_argument("--output", type=Path, default=Path("local_data/risk_budget.json"))

    managed = sub.add_parser("backtest-risk", help="Past-only dynamic risk budgets with a cash sleeve")
    managed.add_argument("--file", type=Path, required=True)
    managed.add_argument("--output", type=Path, default=Path("local_data/risk_managed_backtest.json"))
    managed.add_argument("--lookback-days", type=int, default=756)
    managed.add_argument("--rebalance-days", type=int, default=63)
    managed.add_argument("--risk-aversion", type=float, default=4.0)
    managed.add_argument("--max-weight", type=float, default=0.60)
    managed.add_argument("--max-leverage", type=float, default=2.0)
    managed.add_argument("--max-volatility", type=float, default=0.30)
    managed.add_argument("--stress-decline", type=float, default=0.30)
    managed.add_argument("--max-stress-loss", type=float, default=0.40)
    managed.add_argument("--maintenance", type=float, default=0.30)
    managed.add_argument("--min-buffer", type=float, default=0.05)
    managed.add_argument("--apr", type=float, default=0.065)
    managed.add_argument("--max-financing-cost", type=float, default=0.08)
    managed.add_argument("--fee-bps", type=float, default=5.0)

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
        elif args.command == "backtest-risk":
            report = backtest_risk_budget(
                args.file,
                lookback_days=args.lookback_days,
                rebalance_days=args.rebalance_days,
                risk_aversion=args.risk_aversion,
                max_weight=args.max_weight,
                max_leverage=args.max_leverage,
                max_annual_volatility=args.max_volatility,
                stress_decline=args.stress_decline,
                max_stress_loss=args.max_stress_loss,
                maintenance_fraction=args.maintenance,
                minimum_maintenance_buffer=args.min_buffer,
                annual_financing_rate=args.apr,
                max_annual_financing_cost=args.max_financing_cost,
                fee_bps=args.fee_bps,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "method": report["method"],
                "data": report["data"],
                "strategy": report["strategy"],
                "benchmark": report["benchmark"],
                "warnings": report["warnings"],
            }
        elif args.command == "risk-check":
            assumptions = json.loads(args.file.read_text(encoding="utf-8"))
            if not isinstance(assumptions, dict):
                raise ValueError("Risk input must be a JSON object")
            candidate = optimize_mean_variance(
                assumptions["expected_returns"],
                assumptions["covariance"],
                leverage=args.leverage,
                risk_aversion=args.risk_aversion,
                max_weight=args.max_weight,
                annual_financing_rate=args.apr,
            )
            report = check_risk_budget(
                candidate["weights"], assumptions["covariance"],
                proposed_leverage=args.leverage,
                max_allowed_leverage=args.max_leverage,
                max_name_weight=args.max_name_weight,
                max_annual_volatility=args.max_volatility,
                stress_decline=args.stress_decline,
                max_stress_loss_fraction=args.max_stress_loss,
                maintenance_fraction=args.maintenance,
                min_maintenance_excess_fraction=args.min_buffer,
                annual_financing_rate=args.apr,
                max_annual_financing_cost_fraction=args.max_financing_cost,
            )
            report["input_metadata"] = assumptions.get("metadata")
            report["candidate_optimizer"] = {
                "method": candidate["method"],
                "risk_aversion": candidate["risk_aversion"],
                "expected_net_return": candidate["expected_net_return"],
                "is_trained_ai": False,
                "warning": "Returns and covariance were estimated from history, not future forecasts."
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            report["written"] = str(args.output)
        elif args.command == "benchmark-spy":
            report = compare_spy(args.cash, args.margin, args.spy)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {key: value for key, value in report.items() if key != "spy_curve"}
            diagnostics = report.get("active_risk")
            if isinstance(diagnostics, dict):
                report["active_risk"] = {
                    key: value for key, value in diagnostics.items()
                    if key != "calendar_years"
                }
                report["active_risk"]["annual_breakdown_saved_to_file"] = True
            report["written"] = str(args.output)
        elif args.command == "audit":
            report = audit_backtests(args.cash, args.margin)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "date_range": report["date_range"],
                "observations": report["observations"],
                "cash": {
                    key: value for key, value in report["cash"].items()
                    if key not in {"calendar_year_returns"}
                },
                "margin": {
                    key: value for key, value in report["margin"].items()
                    if key not in {"calendar_year_returns"}
                },
                "benchmark": report["benchmark"],
                "conclusion": report["conclusion"],
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
                margin_policy=args.margin_policy,
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
