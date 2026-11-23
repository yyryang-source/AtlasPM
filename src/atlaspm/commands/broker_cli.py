"""Stable public broker_cli interface: delegates to small command families.

All command flags, output schemas, error behavior and permissions remain unchanged.
"""

from __future__ import annotations

from atlaspm.commands import broker_accounts_cli, broker_risk_cli, broker_audits_cli, broker_paper_cli

COMMANDS = frozenset({
    "institutional-check",
    "ledger-report",
    "plan-equity",
    "plan-basket",
    "oms-replay",
    "broker-reconcile",
    "ibkr-readonly",
    "ibkr-map-check",
    "ibkr-financing-check",
    "ibkr-margin-monitor",
    "ibkr-stress",
    "ibkr-multiasset-budget",
    "ibkr-reverse-stress",
    "ibkr-risk-audit",
    "ibkr-audit-diff",
    "ibkr-dashboard",
})


def run(args) -> dict:
    if args.command in broker_accounts_cli.COMMANDS:
        return broker_accounts_cli.run(args)
    elif args.command in broker_risk_cli.COMMANDS:
        return broker_risk_cli.run(args)
    elif args.command in broker_audits_cli.COMMANDS:
        return broker_audits_cli.run(args)
    elif args.command in broker_paper_cli.COMMANDS:
        return broker_paper_cli.run(args)
    raise ValueError("Unsupported broker_cli command")


def register_parsers(sub) -> None:
    broker_paper_cli.register_institutional_check(sub)
    broker_paper_cli.register_ledger_report(sub)
    broker_paper_cli.register_plan_equity(sub)
    broker_paper_cli.register_plan_basket(sub)
    broker_paper_cli.register_oms_replay(sub)
    broker_paper_cli.register_broker_reconcile(sub)
    broker_accounts_cli.register_ibkr_readonly(sub)
    broker_accounts_cli.register_ibkr_map_check(sub)
    broker_accounts_cli.register_ibkr_financing_check(sub)
    broker_accounts_cli.register_ibkr_margin_monitor(sub)
    broker_risk_cli.register_ibkr_stress(sub)
    broker_risk_cli.register_ibkr_multiasset_budget(sub)
    broker_risk_cli.register_ibkr_reverse_stress(sub)
    broker_audits_cli.register_ibkr_risk_audit(sub)
    broker_audits_cli.register_ibkr_audit_diff(sub)
    broker_audits_cli.register_ibkr_dashboard(sub)


