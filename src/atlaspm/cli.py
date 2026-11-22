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
from atlaspm.institutional_gate import assess_institutional_intent
from atlaspm.institutional_ledger import build_institutional_ledger
from atlaspm.institutional_rebalance import plan_equity_trade
from atlaspm.institutional_batch import propose_multi_asset_batch
from atlaspm.paper_oms import replay_paper_orders
from atlaspm.broker_reconcile import reconcile_imported_broker_snapshot
from atlaspm.ibkr_gateway import (
    LocalGatewayTransport, FixtureGatewayTransport,
    collect_ibkr_readonly_account, redacted_console_summary,
    require_private_output,
)
from atlaspm.ibkr_mapping import diagnose_ibkr_contract_mapping, mapping_redacted_summary
from atlaspm.ibkr_financing import reconcile_ibkr_financing_margin, financing_redacted_summary
from atlaspm.ibkr_margin_monitor import collect_ibkr_margin_monitor, redacted_margin_console
from atlaspm.ibkr_margin_stress import run_margin_stress, redacted_stress_console


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
    managed.add_argument("--max-daily-tail-loss", type=float, default=None,
                         help="Optional maximum historical one-day ES as fraction of equity")
    managed.add_argument("--tail-fraction", type=float, default=0.05,
                         help="Worst historical tail share, >0 and <=0.25")

    institutional = sub.add_parser(
        "institutional-check",
        help="Institutional-style fail-closed paper order intent preflight",
    )
    institutional.add_argument("--file", type=Path, required=True)
    institutional.add_argument(
        "--output", type=Path, default=Path("local_data/institutional_preflight.json")
    )

    ledger = sub.add_parser(
        "ledger-report", help="Read-only cash, stock, Treasury and option accounting"
    )
    ledger.add_argument("--file", type=Path, required=True)
    ledger.add_argument(
        "--output", type=Path, default=Path("local_data/institutional_ledger_report.json")
    )

    planner = sub.add_parser(
        "plan-equity",
        help="Read-only BUY/SELL equity proposal against ledger and RMS",
    )
    planner.add_argument("--ledger", type=Path, required=True)
    planner.add_argument("--plan", type=Path, required=True)
    planner.add_argument(
        "--output", type=Path, default=Path("local_data/equity_trade_plan.json")
    )

    batch = sub.add_parser(
        "plan-basket",
        help="Read-only combined equities, UST, protective put and margin debt plan",
    )
    batch.add_argument("--ledger", type=Path, required=True)
    batch.add_argument("--plan", type=Path, required=True)
    batch.add_argument(
        "--output", type=Path, default=Path("local_data/institutional_batch_plan.json")
    )

    paper = sub.add_parser(
        "oms-replay",
        help="Offline paper OMS event replay for an approved synthetic multi-asset basket",
    )
    paper.add_argument("--ledger", type=Path, required=True)
    paper.add_argument("--plan", type=Path, required=True)
    paper.add_argument("--events", type=Path, required=True)
    paper.add_argument(
        "--output", type=Path, default=Path("local_data/paper_oms_replay.json")
    )

    reconcile = sub.add_parser(
        "broker-reconcile",
        help="Offline, read-only broker-export vs local ledger reconciliation",
    )
    reconcile.add_argument("--ledger", type=Path, required=True)
    reconcile.add_argument("--snapshot", type=Path, required=True)
    reconcile.add_argument(
        "--output", type=Path,
        default=Path("local_data/broker_reconciliation.json"),
    )

    ibkr = sub.add_parser(
        "ibkr-readonly",
        help="Explicit-opt-in local IBKR Client Portal Gateway GET-only account capture",
    )
    ibkr.add_argument("--account", required=True)
    ibkr.add_argument("--fixture", type=Path, default=None)
    ibkr.add_argument("--allow-local-gateway", action="store_true")
    ibkr.add_argument("--url", default="https://localhost:5000/v1/api")
    ibkr.add_argument("--ca-file", type=Path, default=None)
    ibkr.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_private_raw.json")
    )

    mapper = sub.add_parser(
        "ibkr-map-check",
        help="Offline, non-executable conid identity and unit mapping diagnostics",
    )
    mapper.add_argument("--capture", type=Path, required=True)
    mapper.add_argument("--mapping", type=Path, required=True)
    mapper.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_mapping_check.json")
    )

    financing = sub.add_parser(
        "ibkr-financing-check",
        help="Offline IBKR financing principal and current margin summary cross-check",
    )
    financing.add_argument("--capture", type=Path, required=True)
    financing.add_argument("--mapping", type=Path, required=True)
    financing.add_argument("--statement", type=Path, required=True)
    financing.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_financing_check.json")
    )

    monitor = sub.add_parser(
        "ibkr-margin-monitor",
        help="GET-only local Gateway portfolio summary monitor (or synthetic fixture)",
    )
    monitor.add_argument("--account", required=True)
    monitor.add_argument("--fixture", type=Path, default=None)
    monitor.add_argument("--allow-local-gateway", action="store_true")
    monitor.add_argument("--url", default="https://localhost:5000/v1/api")
    monitor.add_argument("--ca-file", type=Path, default=None)
    monitor.add_argument("--max-age-seconds", type=int, default=600)
    monitor.add_argument("--min-buffer-ratio", type=float, default=0.15)
    monitor.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_margin_monitor.json")
    )

    stress = sub.add_parser(
        "ibkr-stress",
        help="Offline hypothetical margin shock and financing-cost scenarios; no execution",
    )
    stress.add_argument("--monitor", type=Path, required=True)
    stress.add_argument("--financing", type=Path, required=True)
    stress.add_argument("--scenarios", type=Path, required=True)
    stress.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_margin_stress.json")
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "ibkr-stress":
            destination = require_private_output(args.output)
            # Historical monitor/finance reports contain account IDs and balances.
            monitor_source = require_private_output(args.monitor)
            financing_source = require_private_output(args.financing)
            try:
                monitor_payload = json.loads(monitor_source.read_text(encoding="utf-8"))
                finance_payload = json.loads(financing_source.read_text(encoding="utf-8"))
                assumptions = json.loads(args.scenarios.read_text(encoding="utf-8"))
                result = run_margin_stress(monitor_payload, finance_payload, assumptions)
            except (ValueError, TypeError, KeyError, OSError):
                raise ValueError(
                    "Local margin stress inputs failed validation; inspect only your private files"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = redacted_stress_console(result, str(args.output))
        elif args.command == "ibkr-margin-monitor":
            destination = require_private_output(args.output)
            if (args.fixture is None) == (not args.allow_local_gateway):
                raise ValueError(
                    "Choose exactly one: --fixture OR --allow-local-gateway"
                )
            try:
                if args.fixture is not None:
                    fixture_data = json.loads(args.fixture.read_text(encoding="utf-8"))
                    transport = FixtureGatewayTransport(fixture_data)
                    fixture_mode = True
                else:
                    transport = LocalGatewayTransport(
                        args.url, ca_file=str(args.ca_file) if args.ca_file else None
                    )
                    fixture_mode = False
                diagnostic = collect_ibkr_margin_monitor(
                    transport, account_id=args.account, fixture_mode=fixture_mode,
                    max_field_age_seconds=args.max_age_seconds,
                    min_buffer_ratio=args.min_buffer_ratio,
                )
            except (OSError, ValueError, TypeError, KeyError):
                raise ValueError(
                    "IBKR margin monitor failed validation or transport; inspect local setup and data"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(diagnostic, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = redacted_margin_console(diagnostic, str(args.output))
        elif args.command == "ibkr-financing-check":
            destination = require_private_output(args.output)
            try:
                capture_payload = json.loads(args.capture.read_text(encoding="utf-8"))
                mapping_payload = json.loads(args.mapping.read_text(encoding="utf-8"))
                margin_payload = json.loads(args.statement.read_text(encoding="utf-8"))
                diagnostic = reconcile_ibkr_financing_margin(
                    capture_payload, mapping_payload, margin_payload
                )
            except (ValueError, KeyError, TypeError) as exc:
                # Never include raw identifiers, amounts or malformed input in stderr.
                raise ValueError("Private financing data failed validation; inspect local inputs") from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(diagnostic, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = financing_redacted_summary(diagnostic, str(args.output))
        elif args.command == "ibkr-map-check":
            destination = require_private_output(args.output)
            capture_payload = json.loads(args.capture.read_text(encoding="utf-8"))
            mapping_payload = json.loads(args.mapping.read_text(encoding="utf-8"))
            diagnostic = diagnose_ibkr_contract_mapping(
                capture_payload, mapping_payload
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(diagnostic, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = mapping_redacted_summary(diagnostic, str(args.output))
        elif args.command == "ibkr-readonly":
            if (args.fixture is None) == (not args.allow_local_gateway):
                raise ValueError(
                    "Choose exactly one: --fixture for no-network test OR --allow-local-gateway"
                )
            if args.fixture is not None:
                fixture_data = json.loads(args.fixture.read_text(encoding="utf-8"))
                transport = FixtureGatewayTransport(fixture_data)
                fixture_mode = True
            else:
                transport = LocalGatewayTransport(
                    args.url,
                    ca_file=str(args.ca_file) if args.ca_file else None,
                )
                fixture_mode = False
            destination = require_private_output(args.output)
            captured = collect_ibkr_readonly_account(
                transport, account_id=args.account,
                fixture_mode=fixture_mode,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(captured, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = redacted_console_summary(captured, args.output)
        elif args.command == "broker-reconcile":
            ledger_payload = json.loads(args.ledger.read_text(encoding="utf-8"))
            broker_payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
            report = reconcile_imported_broker_snapshot(
                ledger_payload, broker_payload
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "method": report["method"],
                "account_id": report["account_id"],
                "decision": report["decision"],
                "matched_position_count": report["matched_position_count"],
                "missing_in_import": report["missing_in_import"],
                "unexpected_in_import": report["unexpected_in_import"],
                "local_net_liquidation": report["local_net_liquidation"],
                "imported_net_liquidation": report["imported_net_liquidation"],
                "imported_balance_sheet_reconciliation_gap": report[
                    "imported_balance_sheet_reconciliation_gap"
                ],
                "snapshot_age_seconds": report["snapshot_age_seconds"],
                "block_reasons": report["block_reasons"],
                "differences": report["differences"],
                "source_authentication_verified": report["source_authentication_verified"],
                "broker_order_margin_preview_verified": report["broker_order_margin_preview_verified"],
                "live_execution_enabled": report["live_execution_enabled"],
                "warnings": report["warnings"],
            }
        elif args.command == "oms-replay":
            base_payload = json.loads(args.ledger.read_text(encoding="utf-8"))
            plan_payload = json.loads(args.plan.read_text(encoding="utf-8"))
            event_payload = json.loads(args.events.read_text(encoding="utf-8"))
            report = replay_paper_orders(base_payload, plan_payload, event_payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            report = {
                "written": str(args.output),
                "method": report["method"],
                "batch_id": report["batch_id"],
                "account_id": report["account_id"],
                "decision": report["decision"],
                "live_execution_enabled": report["live_execution_enabled"],
                "duplicate_events_ignored": report["duplicate_events_ignored"],
                "unresolved_orders": report["unresolved_orders"],
                "post_fill_risk_breaches": report["post_fill_risk_breaches"],
                "post_fill_risk_snapshot_count": len(report["post_fill_risk_snapshots"]),
                "audit_root_sha256": report["audit_root_sha256"],
                "audit_hash_chain_self_consistent": report["audit_hash_chain_self_consistent"],
                "broker_margin_rechecked_after_fills": report["broker_margin_rechecked_after_fills"],
                "orders": report["orders"],
                "account_replay": {
                    key: value for key, value in report["account_replay"].items()
                    if key != "positions"
                },
                "warnings": report["warnings"],
            }
        elif args.command == "plan-basket":
            base_payload = json.loads(args.ledger.read_text(encoding="utf-8"))
            batch_payload = json.loads(args.plan.read_text(encoding="utf-8"))
            report = propose_multi_asset_batch(base_payload, batch_payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "method": report["method"],
                "decision": report["decision"],
                "batch_id": report["batch_id"],
                "account_id": report["account_id"],
                "before": report["before"],
                "pro_forma_unexecuted": {
                    key: value for key, value in report["pro_forma_unexecuted"].items()
                    if key != "positions"
                },
                "leg_risk_verdicts": report["leg_risk_verdicts"],
                "block_reasons": report["block_reasons"],
                "live_execution_enabled": report["live_execution_enabled"],
                "warnings": report["warnings"],
            }
        elif args.command == "plan-equity":
            ledger_payload = json.loads(args.ledger.read_text(encoding="utf-8"))
            plan_payload = json.loads(args.plan.read_text(encoding="utf-8"))
            report = plan_equity_trade(ledger_payload, plan_payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            report = {
                "written": str(args.output),
                "decision": report["decision"],
                "risk_state": report["rms"]["risk_state"],
                "client_order_id": report["client_order_id"],
                "account_id": report["account_id"],
                "action": report["action"],
                "symbol": report["symbol"],
                "shares": report["shares"],
                "trade_notional": report["trade_notional"],
                "estimated_fee": report["estimated_fee"],
                "pro_forma_after_unexecuted_trade": report["pro_forma_after_unexecuted_trade"],
                "eligible_for_paper_intent": report["eligible_for_paper_intent"],
                "live_execution_enabled": report["live_execution_enabled"],
                "block_reasons": report["block_reasons"],
                "warnings": report["warnings"],
            }
        elif args.command == "ledger-report":
            payload = json.loads(args.file.read_text(encoding="utf-8"))
            report = build_institutional_ledger(payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "method": report["method"],
                "valuation_at": report["valuation_at"],
                "consolidated": report["consolidated"],
                "accounts": [
                    {
                        "account_id": account["account_id"],
                        "account_type": account["account_type"],
                        "net_liquidation_calculated": account["net_liquidation_calculated"],
                        "broker_reported_net_liquidation": account["broker_reported_net_liquidation"],
                        "reconciliation_difference": account["reconciliation_difference"],
                        "account_ledger_ready_for_risk_review": account["account_ledger_ready_for_risk_review"],
                        "alerts": account["alerts"],
                    }
                    for account in report["accounts"]
                ],
                "warnings": report["warnings"],
            }
        elif args.command == "institutional-check":
            payload = json.loads(args.file.read_text(encoding="utf-8"))
            report = assess_institutional_intent(payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report["written"] = str(args.output)
        elif args.command == "analyze":
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
