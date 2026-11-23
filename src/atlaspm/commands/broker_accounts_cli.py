"""broker_accounts_cli: offline AtlasPM command handlers; no live orders.

Focused CLI adapter, not the underlying quantitative strategy/model.
"""

from __future__ import annotations

import json
from pathlib import Path
from atlaspm.execution.ibkr_gateway import LocalGatewayTransport, FixtureGatewayTransport, collect_ibkr_readonly_account, redacted_console_summary, require_private_output
from atlaspm.execution.ibkr_mapping import diagnose_ibkr_contract_mapping, mapping_redacted_summary
from atlaspm.risk.ibkr_financing import reconcile_ibkr_financing_margin, financing_redacted_summary
from atlaspm.risk.ibkr_margin_monitor import collect_ibkr_margin_monitor, redacted_margin_console

COMMANDS = frozenset(["ibkr-readonly","ibkr-map-check","ibkr-financing-check","ibkr-margin-monitor"])


def run(args) -> dict:
    if args.command == "ibkr-margin-monitor":
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
    else:
        raise ValueError("Unsupported broker_accounts_cli command")
    return report


def register_ibkr_readonly(sub) -> None:
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


def register_ibkr_map_check(sub) -> None:
    mapper = sub.add_parser(
        "ibkr-map-check",
        help="Offline, non-executable conid identity and unit mapping diagnostics",
    )
    mapper.add_argument("--capture", type=Path, required=True)
    mapper.add_argument("--mapping", type=Path, required=True)
    mapper.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_mapping_check.json")
    )


def register_ibkr_financing_check(sub) -> None:
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


def register_ibkr_margin_monitor(sub) -> None:
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

