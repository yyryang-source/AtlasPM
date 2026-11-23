"""broker_risk_cli: offline AtlasPM command handlers; no live orders.

Focused CLI adapter, not the underlying quantitative strategy/model.
"""

from __future__ import annotations

import json
from pathlib import Path
from atlaspm.execution.ibkr_gateway import require_private_output
from atlaspm.risk.ibkr_margin_stress import run_margin_stress, redacted_stress_console
from atlaspm.risk.ibkr_multiasset_budget import analyze_multiasset_risk_budget, redacted_multiasset_summary
from atlaspm.risk.ibkr_reverse_stress import reverse_margin_stress, redacted_reverse_summary

COMMANDS = frozenset(["ibkr-stress","ibkr-multiasset-budget","ibkr-reverse-stress"])


def run(args) -> dict:
    if args.command == "ibkr-reverse-stress":
        destination = require_private_output(args.output)
        upstream_path = require_private_output(args.budget)
        try:
            upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
            assumptions = json.loads(args.assumptions.read_text(encoding="utf-8"))
            result = reverse_margin_stress(upstream, assumptions)
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError(
                "Local reverse stress inputs are invalid; review only private files"
            ) from None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        report = redacted_reverse_summary(result, str(args.output))

    elif args.command == "ibkr-multiasset-budget":
        destination = require_private_output(args.output)
        prior_path = require_private_output(args.stress)
        try:
            prior = json.loads(prior_path.read_text(encoding="utf-8"))
            inputs = json.loads(args.assumptions.read_text(encoding="utf-8"))
            result = analyze_multiasset_risk_budget(prior, inputs)
        except (ValueError, TypeError, KeyError, OSError):
            raise ValueError(
                "Local multiasset risk inputs failed validation; inspect private files"
            ) from None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        report = redacted_multiasset_summary(result, str(args.output))

    elif args.command == "ibkr-stress":
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
    else:
        raise ValueError("Unsupported broker_risk_cli command")
    return report


def register_ibkr_stress(sub) -> None:
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


def register_ibkr_multiasset_budget(sub) -> None:
    multiasset = sub.add_parser(
        "ibkr-multiasset-budget",
        help="Offline hypothetical correlation stress and financing risk-budget check",
    )
    multiasset.add_argument("--stress", type=Path, required=True)
    multiasset.add_argument("--assumptions", type=Path, required=True)
    multiasset.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_multiasset_budget.json")
    )


def register_ibkr_reverse_stress(sub) -> None:
    reverse = sub.add_parser(
        "ibkr-reverse-stress",
        help="Offline reverse stress: assumed market decline needed to hit margin and buffer limits",
    )
    reverse.add_argument("--budget", type=Path, required=True)
    reverse.add_argument("--assumptions", type=Path, required=True)
    reverse.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_reverse_stress.json")
    )

