"""quant_backtesting_cli: offline AtlasPM command handlers; no live orders.

Focused CLI adapter, not the underlying quantitative strategy/model.
"""

from __future__ import annotations

import json
from pathlib import Path
from atlaspm.engine.mean_variance import optimize_mean_variance
from atlaspm.engine.backtest import backtest_walk_forward
from atlaspm.monitoring.audit import audit_backtests
from atlaspm.monitoring.benchmark import compare_spy
from atlaspm.risk.risk_budget import check_risk_budget
from atlaspm.risk.risk_backtest import backtest_risk_budget

COMMANDS = frozenset(["backtest","audit","benchmark-spy","risk-check","backtest-risk"])


def run(args) -> dict:
    if args.command == "backtest-risk":
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
            max_daily_tail_loss=args.max_daily_tail_loss,
            tail_fraction=args.tail_fraction,
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
    else:
        raise ValueError("Unsupported quant_backtesting_cli command")
    return report


def register_backtest(sub) -> None:
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


def register_audit(sub) -> None:
    audit = sub.add_parser("audit", help="Compare saved cash and margin backtests")
    audit.add_argument("--cash", type=Path, required=True)
    audit.add_argument("--margin", type=Path, required=True)
    audit.add_argument("--output", type=Path, default=Path("local_data/risk_audit.json"))


def register_benchmark_spy(sub) -> None:
    benchmark = sub.add_parser("benchmark-spy", help="Exact-date independent SPY comparison")
    benchmark.add_argument("--cash", type=Path, required=True)
    benchmark.add_argument("--margin", type=Path, required=True)
    benchmark.add_argument("--spy", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, default=Path("local_data/spy_comparison.json"))


def register_risk_check(sub) -> None:
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


def register_backtest_risk(sub) -> None:
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
    managed.add_argument("--max-daily-tail-loss", type=float, default=None,
                         help="Optional maximum historical one-day ES as fraction of equity")
    managed.add_argument("--tail-fraction", type=float, default=0.05,
                         help="Worst historical tail share, >0 and <=0.25")

