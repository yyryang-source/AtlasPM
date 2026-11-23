"""broker_audits_cli: offline AtlasPM command handlers; no live orders.

Focused CLI adapter, not the underlying quantitative strategy/model.
"""

from __future__ import annotations

import json
from pathlib import Path
from atlaspm.execution.ibkr_gateway import require_private_output
from atlaspm.monitoring.ibkr_risk_chain_audit import audit_ibkr_risk_chain, redacted_audit_summary
from atlaspm.monitoring.ibkr_risk_audit_diff import compare_ibkr_risk_audits, redacted_audit_diff_summary
from atlaspm.monitoring.ibkr_dashboard import build_dashboard_view, render_dashboard_html, redacted_dashboard_console

COMMANDS = frozenset(["ibkr-risk-audit","ibkr-audit-diff","ibkr-dashboard"])


def run(args) -> dict:
    if args.command == "ibkr-dashboard":
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".html":
            raise ValueError("Local dashboard output must be an .html file")
        base = args.folder.expanduser().resolve()
        project_local_data = (Path.cwd().resolve() / "local_data").resolve()
        if base != project_local_data:
            raise ValueError("Dashboard inputs must come from the project local_data/ folder")
        names = {
            "monitor": "margin_monitor_fixture.json",
            "financing": "ibkr_financing_check.json",
            "stress": "ibkr_margin_stress.json",
            "multiasset": "ibkr_multiasset_budget.json",
            "reverse": "ibkr_reverse_stress.json",
            "previous_audit": "ibkr_risk_audit.json",
            "latest_audit": "ibkr_risk_audit_second.json",
            "audit_diff": "ibkr_risk_audit_diff.json",
        }
        sources = {
            name: require_private_output(base / filename)
            for name, filename in names.items()
        }
        if destination in set(sources.values()):
            raise ValueError("Refusing to overwrite a private dashboard input")
        try:
            loaded = {
                name: json.loads(path.read_text(encoding="utf-8"))
                for name, path in sources.items()
            }
            reports = {
                name: loaded[name]
                for name in ("monitor", "financing", "stress", "multiasset", "reverse")
            }
            view = build_dashboard_view(
                reports, loaded["previous_audit"], loaded["latest_audit"],
                loaded["audit_diff"],
            )
            page = render_dashboard_html(view)
        except (OSError, ValueError, TypeError, KeyError):
            raise ValueError(
                "Local dashboard inputs failed validation; inspect private files"
            ) from None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(page, encoding="utf-8")
        report = redacted_dashboard_console(view, str(args.output))

    elif args.command == "ibkr-audit-diff":
        destination = require_private_output(args.output)
        previous_path = require_private_output(args.previous)
        current_path = require_private_output(args.current)
        if destination in {previous_path, current_path}:
            raise ValueError("Refusing to overwrite an audit comparison input")
        try:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            current = json.loads(current_path.read_text(encoding="utf-8"))
            result = compare_ibkr_risk_audits(previous, current)
        except (ValueError, TypeError, KeyError, OSError):
            raise ValueError(
                "Private audit comparison failed validation; inspect local files only"
            ) from None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = redacted_audit_diff_summary(result, str(args.output))

    elif args.command == "ibkr-risk-audit":
        destination = require_private_output(args.output)
        source_paths = {
            "monitor": require_private_output(args.monitor),
            "financing": require_private_output(args.financing),
            "stress": require_private_output(args.stress),
            "multiasset": require_private_output(args.multiasset),
            "reverse": require_private_output(args.reverse),
        }
        baseline_path = require_private_output(args.compare) if args.compare else None
        if destination in set(source_paths.values()) or destination == baseline_path:
            raise ValueError("Refusing to overwrite a private risk audit input")
        try:
            reports = {
                name: json.loads(path.read_text(encoding="utf-8"))
                for name, path in source_paths.items()
            }
            previous = (
                json.loads(baseline_path.read_text(encoding="utf-8"))
                if baseline_path else None
            )
            result = audit_ibkr_risk_chain(reports, baseline_audit=previous)
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError(
                "Local audit reports failed validation; inspect private files only"
            ) from None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = redacted_audit_summary(result, str(args.output))
    else:
        raise ValueError("Unsupported broker_audits_cli command")
    return report


def register_ibkr_risk_audit(sub) -> None:
    chain = sub.add_parser(
        "ibkr-risk-audit",
        help="Audit five private offline IBKR risk diagnostics; no broker proof or execution",
    )
    chain.add_argument("--monitor", type=Path, required=True)
    chain.add_argument("--financing", type=Path, required=True)
    chain.add_argument("--stress", type=Path, required=True)
    chain.add_argument("--multiasset", type=Path, required=True)
    chain.add_argument("--reverse", type=Path, required=True)
    chain.add_argument("--compare", type=Path, default=None)
    chain.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_risk_audit.json")
    )


def register_ibkr_audit_diff(sub) -> None:
    audit_diff = sub.add_parser(
        "ibkr-audit-diff",
        help="Compare two private unsigned risk-audit files; no broker provenance claim",
    )
    audit_diff.add_argument("--previous", type=Path, required=True)
    audit_diff.add_argument("--current", type=Path, required=True)
    audit_diff.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_risk_audit_diff.json")
    )


def register_ibkr_dashboard(sub) -> None:
    dashboard = sub.add_parser(
        "ibkr-dashboard",
        help="Generate local offline redacted HTML dashboard from existing private reports",
    )
    dashboard.add_argument(
        "--folder", type=Path, default=Path("local_data"),
        help="Private local_data folder containing v0.4.10–v0.4.15 reports",
    )
    dashboard.add_argument(
        "--output", type=Path, default=Path("local_data/atlaspm_dashboard.html"),
    )

