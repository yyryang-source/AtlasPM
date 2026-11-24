"""Offline option, Treasury and hedge research commands."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from atlaspm.data.lean_option_quotes import import_lean_option_quotes
from atlaspm.data.option_history import OptionHistory
from atlaspm.data.treasury_history import TreasuryHistory
from atlaspm.execution.ibkr_gateway import require_private_output
from atlaspm.hedging.multiasset_hedge import MultiassetSettings, compare_multiasset_hedge
from atlaspm.strategies.bonds.treasury_replay import replay_treasury_holding
from atlaspm.strategies.options.option_replay import replay_long_options
from atlaspm.strategies.options.protective_put import ProtectivePutSettings, compare_protective_put, load_stock_marks

COMMANDS = frozenset({
    'option-import-lean-quotes',
    'treasury-replay',
    'multiasset-hedge-backtest',
    'protective-put-backtest',
    'option-history-replay',
})


def _put_settings(args: argparse.Namespace, cash_usd: float) -> ProtectivePutSettings:
    return ProtectivePutSettings(
        underlying=args.underlying.strip().upper(), shares=args.shares, cash_usd=cash_usd,
        fee_per_contract_usd=args.fee_per_contract_usd, otm_fraction=args.otm_fraction,
        target_dte=args.target_dte, max_dte=args.max_dte, roll_sessions=args.roll_sessions,
        max_quote_age_minutes=args.max_quote_age_minutes, eod_snapshot=args.eod_snapshot,
    )


def run(args) -> dict:
    if args.command == 'option-import-lean-quotes':
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".csv" or destination.exists():
            raise ValueError("LEAN quote import requires a new private .csv output")
        report = import_lean_option_quotes(args.archives, args.contracts, destination)
        report["written"] = destination.relative_to(Path.cwd().resolve()).as_posix()
    elif args.command == 'treasury-replay':
        destination = require_private_output(args.output)
        sources = {path.expanduser().resolve() for path in (args.contracts, args.payments, args.quotes)}
        if destination.suffix.lower() != ".json" or destination in sources:
            raise ValueError("Treasury replay requires a private .json output distinct from inputs")
        history = TreasuryHistory(args.contracts, args.payments, args.quotes)
        result = replay_treasury_holding(history, args.cusip.strip().upper(), args.face_usd, args.dates,
                                         initial_cash_usd=args.cash_usd, max_quote_age_days=args.max_quote_age_days,
                                         shock_bps=args.shock_bps)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        last_mark = result["valuations"][-1]
        report = {"written": destination.relative_to(Path.cwd().resolve()).as_posix(),
                  "ending_nav_usd": last_mark["nav_usd"], "coupon_paid_usd": last_mark["coupon_paid_to_date_usd"],
                  "principal_paid_usd": last_mark["principal_paid_to_date_usd"], "live_execution_enabled": False}
    elif args.command == 'multiasset-hedge-backtest':
        destination = require_private_output(args.output)
        sources = {path.expanduser().resolve() for path in (
            args.contracts, args.quotes, args.settlements, args.stock_marks,
            args.treasury_contracts, args.treasury_payments, args.treasury_quotes,
        )}
        if destination.suffix.lower() != ".json" or destination in sources:
            raise ValueError("Multi-asset hedge backtest requires a private .json output distinct from inputs")
        options = OptionHistory(args.contracts, args.quotes, args.settlements)
        bonds = TreasuryHistory(args.treasury_contracts, args.treasury_payments, args.treasury_quotes)
        settings = MultiassetSettings(
            initial_capital_usd=args.initial_capital_usd, minimum_cash_usd=args.minimum_cash_usd,
            treasury_cusip=args.treasury_cusip.strip().upper(), treasury_face_usd=args.treasury_face_usd,
            stock_entry_cost_bps=args.stock_entry_cost_bps, treasury_entry_cost_bps=args.treasury_entry_cost_bps,
            treasury_shock_bps=args.treasury_shock_bps, treasury_max_quote_age_days=args.treasury_max_quote_age_days,
        )
        result = compare_multiasset_hedge(
            options, bonds, load_stock_marks(args.stock_marks), _put_settings(args, 0.0), settings,
            treasury_closed=frozenset(args.treasury_closed),
            stress_window=tuple(args.stress_window) if args.stress_window else None,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        report = {"written": destination.relative_to(Path.cwd().resolve()).as_posix(),
                  "sessions": len(result["curve"]), "performance": result["performance"],
                  "stress_window": result["stress_window"], "option_costs": result["option_costs"],
                  "live_execution_enabled": False}
    elif args.command == 'protective-put-backtest':
        destination = require_private_output(args.output)
        sources = {path.expanduser().resolve() for path in (
            args.contracts, args.quotes, args.settlements, args.stock_marks,
        )}
        if destination.suffix.lower() != ".json" or destination in sources:
            raise ValueError("Protective put backtest requires a private .json output distinct from inputs")
        history = OptionHistory(args.contracts, args.quotes, args.settlements)
        result = compare_protective_put(history, load_stock_marks(args.stock_marks), _put_settings(args, args.cash_usd))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        report = {"written": destination.relative_to(Path.cwd().resolve()).as_posix(),
                  "hedged": result["hedged"], "unhedged": result["unhedged"],
                  "sessions": len(result["curve"]), "fills": len(result["option_replay"]["fills"]),
                  **{metric: result["option_replay"][metric] for metric in (
                      "premium_paid_usd", "premium_sold_usd", "execution_fees_usd", "spread_cost_vs_mid_usd",
                  )},
                  "live_execution_enabled": False}
    elif args.command == 'option-history-replay':
        destination = require_private_output(args.output)
        sources = {path.expanduser().resolve() for path in (
            args.contracts, args.quotes, args.settlements, args.plan,
        )}
        if destination.suffix.lower() != ".json" or destination in sources:
            raise ValueError("Option replay requires a private .json output distinct from inputs")
        history = OptionHistory(args.contracts, args.quotes, args.settlements)
        result = replay_long_options(history, json.loads(args.plan.read_text(encoding="utf-8-sig")))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                               encoding="utf-8")
        report = {
            "written": destination.relative_to(Path.cwd().resolve()).as_posix(),
            "fill_count": len(result["fills"]), "expiration_count": len(result["expirations"]),
            "premium_paid_usd": result["premium_paid_usd"], "premium_sold_usd": result["premium_sold_usd"],
            "execution_fees_usd": result["execution_fees_usd"],
            "spread_cost_vs_mid_usd": result["spread_cost_vs_mid_usd"], "live_execution_enabled": False,
        }
    else:
        raise ValueError("Unsupported research command")
    return report


def register_parsers(sub) -> None:
    option_replay = sub.add_parser(
        "option-history-replay", help="Replay long options against historical quotes and official settlements",
    )
    for option in ("contracts", "quotes", "settlements", "plan"):
        option_replay.add_argument(f"--{option}", type=Path, required=True)
    option_replay.add_argument("--output", type=Path, default=Path("local_data/option_history_replay.json"))

    lean_quotes = sub.add_parser("option-import-lean-quotes", help="Normalize LEAN US minute option quote ZIPs")
    lean_quotes.add_argument("--zip", dest="archives", type=Path, nargs="+", required=True)
    lean_quotes.add_argument("--contracts", type=Path, required=True)
    lean_quotes.add_argument("--output", type=Path, default=Path("local_data/option_quotes.csv"))

    put_arguments = argparse.ArgumentParser(add_help=False)
    for option in ("contracts", "quotes", "settlements", "stock-marks"):
        put_arguments.add_argument(f"--{option}", type=Path, required=True)
    put_arguments.add_argument("--underlying", required=True)
    put_arguments.add_argument("--shares", type=int, required=True)
    put_arguments.add_argument("--otm-fraction", type=float, default=0.05)
    put_arguments.add_argument("--target-dte", type=int, default=45)
    put_arguments.add_argument("--max-dte", type=int, default=65)
    put_arguments.add_argument("--roll-sessions", type=int, default=21)
    put_arguments.add_argument("--fee-per-contract-usd", type=float, default=1.0)
    put_arguments.add_argument("--max-quote-age-minutes", type=int, default=1)
    put_arguments.add_argument("--eod-snapshot", action="store_true", help="Input quote times represent modeled EOD snapshots")

    protective_put = sub.add_parser("protective-put-backtest", parents=[put_arguments],
                                     help="Compare a rolling put hedge with the same stock holding")
    protective_put.add_argument("--cash-usd", type=float, required=True)
    protective_put.add_argument("--output", type=Path, default=Path("local_data/protective_put_backtest.json"))

    treasury = sub.add_parser("treasury-replay", help="Mark a settled Treasury holding and replay its cash flows")
    for option in ("contracts", "payments", "quotes"):
        treasury.add_argument(f"--{option}", type=Path, required=True)
    treasury.add_argument("--cusip", required=True)
    treasury.add_argument("--face-usd", type=int, required=True)
    treasury.add_argument("--dates", nargs="+", type=date.fromisoformat, required=True)
    treasury.add_argument("--cash-usd", type=float, default=0)
    treasury.add_argument("--max-quote-age-days", type=int, default=3)
    treasury.add_argument("--shock-bps", type=float, default=100)
    treasury.add_argument("--output", type=Path, default=Path("local_data/treasury_replay.json"))

    multiasset = sub.add_parser("multiasset-hedge-backtest", parents=[put_arguments],
                                help="Compare stock, stock/Treasury and stock/Treasury/put with equal opening capital")
    for option in ("treasury-contracts", "treasury-payments", "treasury-quotes"):
        multiasset.add_argument(f"--{option}", type=Path, required=True)
    multiasset.add_argument("--treasury-cusip", required=True)
    multiasset.add_argument("--treasury-face-usd", type=int, required=True)
    multiasset.add_argument("--initial-capital-usd", type=float, required=True)
    multiasset.add_argument("--minimum-cash-usd", type=float, default=0)
    multiasset.add_argument("--stock-entry-cost-bps", type=float, default=5)
    multiasset.add_argument("--treasury-entry-cost-bps", type=float, default=5)
    multiasset.add_argument("--treasury-shock-bps", type=float, default=100)
    multiasset.add_argument("--treasury-max-quote-age-days", type=int, default=3)
    multiasset.add_argument("--treasury-closed", nargs="+", type=date.fromisoformat, default=(),
                            help="Dates when the stock exchange was open and the Treasury market was closed")
    multiasset.add_argument("--stress-window", nargs=2, type=date.fromisoformat, metavar=("START", "END"))
    multiasset.add_argument("--output", type=Path, default=Path("local_data/multiasset_hedge_backtest.json"))
