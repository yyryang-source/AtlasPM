"""broker_paper_cli: offline AtlasPM command handlers; no live orders.

Focused CLI adapter, not the underlying quantitative strategy/model.
"""

from __future__ import annotations

import json
from pathlib import Path
from atlaspm.execution.institutional_gate import assess_institutional_intent
from atlaspm.execution.institutional_ledger import build_institutional_ledger
from atlaspm.execution.institutional_rebalance import plan_equity_trade
from atlaspm.execution.institutional_batch import propose_multi_asset_batch
from atlaspm.execution.paper_oms import replay_paper_orders
from atlaspm.execution.broker_reconcile import reconcile_imported_broker_snapshot

COMMANDS = frozenset(["institutional-check","ledger-report","plan-equity","plan-basket","oms-replay","broker-reconcile"])


def run(args) -> dict:
    if args.command == "broker-reconcile":
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
    else:
        raise ValueError("Unsupported broker_paper_cli command")
    return report


def register_institutional_check(sub) -> None:
    institutional = sub.add_parser(
        "institutional-check",
        help="Institutional-style fail-closed paper order intent preflight",
    )
    institutional.add_argument("--file", type=Path, required=True)
    institutional.add_argument(
        "--output", type=Path, default=Path("local_data/institutional_preflight.json")
    )


def register_ledger_report(sub) -> None:
    ledger = sub.add_parser(
        "ledger-report", help="Read-only cash, stock, Treasury and option accounting"
    )
    ledger.add_argument("--file", type=Path, required=True)
    ledger.add_argument(
        "--output", type=Path, default=Path("local_data/institutional_ledger_report.json")
    )


def register_plan_equity(sub) -> None:
    planner = sub.add_parser(
        "plan-equity",
        help="Read-only BUY/SELL equity proposal against ledger and RMS",
    )
    planner.add_argument("--ledger", type=Path, required=True)
    planner.add_argument("--plan", type=Path, required=True)
    planner.add_argument(
        "--output", type=Path, default=Path("local_data/equity_trade_plan.json")
    )


def register_plan_basket(sub) -> None:
    batch = sub.add_parser(
        "plan-basket",
        help="Read-only combined equities, UST, protective put and margin debt plan",
    )
    batch.add_argument("--ledger", type=Path, required=True)
    batch.add_argument("--plan", type=Path, required=True)
    batch.add_argument(
        "--output", type=Path, default=Path("local_data/institutional_batch_plan.json")
    )


def register_oms_replay(sub) -> None:
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


def register_broker_reconcile(sub) -> None:
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

