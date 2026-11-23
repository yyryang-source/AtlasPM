"""stock_data_cli: offline AtlasPM command handlers; no live orders.

Focused CLI adapter, not the underlying quantitative strategy/model.
"""

from __future__ import annotations

import json
from pathlib import Path
from atlaspm.execution.ibkr_gateway import require_private_output
from atlaspm.data.us_market_universe import build_us_listed_universe
from atlaspm.data.us_market_prices import sync_us_market_prices
from atlaspm.data.us_market_quality import audit_current_us_stock_prices
from atlaspm.data.corporate_action_reconciliation import reconcile_reverse_splits

COMMANDS = frozenset(["stock-universe","stock-market-audit","stock-sync-market","stock-split-reconcile"])


def run(args) -> dict:
    if args.command == "stock-split-reconcile":
        destination = require_private_output(args.report)
        if destination.suffix.lower() != ".json":
            raise ValueError("Split audit report must be private JSON")
        output = require_private_output(args.output) if args.output else None
        if output is not None and output.suffix.lower() != ".csv":
            raise ValueError("Reconciled price output must be private CSV")
        inputs = {args.prices.expanduser().resolve(), args.manifest.expanduser().resolve()}
        if destination in inputs or (output is not None and destination == output):
            raise ValueError("Report cannot overwrite source inputs or output prices")
        result = reconcile_reverse_splits(
            args.prices, args.manifest, output_path=output,
            apply_confirmed=args.apply_confirmed,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "status": result["status"],
            "events_requiring_reconciliation": result["events_requiring_reconciliation"],
            "events_requiring_manual_review": result["events_requiring_manual_review"],
            "event_checks": result["event_checks"],
            "corrected_prices_path": result["corrected_prices_path"],
            "modified_rows": result.get("modified_rows"),
            "report": str(destination.relative_to(Path.cwd().resolve())),
            "research_only": True,
            "live_execution_enabled": False,
        }

    elif args.command == "stock-universe":
        destination = require_private_output(args.output)
        report_path = require_private_output(args.report)
        if destination.suffix.lower() != ".csv" or report_path.suffix.lower() != ".json":
            raise ValueError("Universe needs .csv output and .json report under local_data")
        if destination == report_path:
            raise ValueError("Universe CSV and report paths must differ")
        sources = {
            p.expanduser().resolve() for p in (args.nasdaq_file, args.other_file) if p is not None
        }
        if destination in sources or report_path in sources:
            raise ValueError("Refusing to overwrite universe source file")
        report = build_us_listed_universe(
            destination, nasdaq_file=args.nasdaq_file, other_file=args.other_file,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report = {
            "listing_count": report["listing_count"],
            "unverified_issue_count": report["unverified_issue_count"],
            "snapshot_utc": report["snapshot_utc"],
            "written": str(destination.relative_to(Path.cwd().resolve())),
            "report": str(report_path.relative_to(Path.cwd().resolve())),
            "live_execution_enabled": False,
            "warning": "Current listings only, NOT historical all stocks.",
        }

    elif args.command == "stock-market-audit":
        destination = require_private_output(args.output)
        source_paths = {
            args.universe.expanduser().resolve(),
            args.prices.expanduser().resolve(),
        }
        if destination.suffix.lower() != ".json":
            raise ValueError("Market audit output must be a private .json")
        if destination in source_paths:
            raise ValueError("Market audit may not overwrite source price/universe files")
        details = audit_current_us_stock_prices(
            universe_path=args.universe,
            prices_path=args.prices,
            as_of=args.as_of,
            min_history_sessions=args.min_history_sessions,
            recent_sessions=args.recent_sessions,
            min_recent_coverage_pct=args.min_recent_coverage_pct,
            min_month_end_coverage_pct=args.min_month_end_coverage_pct,
            min_eligible_coverage_pct=args.min_eligible_coverage_pct,
            min_eligible_names=args.min_eligible_names,
            max_manifest_age_days=args.max_manifest_age_days,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(details, indent=2, ensure_ascii=False, sort_keys=True,
                       allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "market_data_status": details["market_data_status"],
            "listed_stock_universe_count": details["listed_stock_universe_count"],
            "stocks_with_exact_decision_close": details["stocks_with_exact_decision_close"],
            "decision_close_coverage_pct": details["decision_close_coverage_pct"],
            "eligible_history_and_freshness": details["eligible_history_and_freshness"],
            "review_reasons": details["review_reasons"],
            "written": str(destination.relative_to(Path.cwd().resolve())),
            "research_only": True,
            "live_execution_enabled": False,
        }

    elif args.command == "stock-sync-market":
        destination = require_private_output(args.output)
        report_path = require_private_output(args.report)
        cache_probe = require_private_output(args.cache_dir / "_atlaspm_probe.json")
        cache_dir = cache_probe.parent
        if destination.suffix.lower() != ".csv" or report_path.suffix.lower() != ".json":
            raise ValueError("Quote output needs .csv and report .json under local_data")
        if len({destination, report_path}) != 2:
            raise ValueError("Quote output and report must differ")
        universe_file = args.universe.expanduser().resolve()
        if destination == universe_file or report_path == universe_file:
            raise ValueError("Refusing to overwrite stock universe manifest")
        if destination.is_relative_to(cache_dir) or report_path.is_relative_to(cache_dir):
            raise ValueError("Quote output/report must not be inside ticker cache")
        details = sync_us_market_prices(
            args.universe, cache_dir, destination,
            start=args.start, end=args.end,
            max_symbols=args.max_symbols, workers=args.workers,
            attempts=args.attempts, batch_size=args.batch_size,
            batch_pause_seconds=args.batch_pause_seconds,
            max_consecutive_failures=args.max_consecutive_failures,
            max_rate_limit_errors=args.max_rate_limit_errors,
            cache_only=args.cache_only,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(details, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "listing_candidates": details["listing_candidates"],
            "covered": details["cached_or_downloaded_symbols"],
            "missing": details["missing_symbols"],
            "coverage_pct": details["coverage_pct"],
            "new_successes": details["new_download_successes"],
            "failed_this_run": details["failed_this_run"],
            "price_rows": details["price_rows"],
            "full_market_coverage": details["full_market_coverage"],
            "download_status": details["download_status"],
            "stopped_early": details["stopped_early"],
            "stop_reason": details["stop_reason"],
            "attempted_downloads": details["attempted_downloads"],
            "not_attempted_due_to_stop": details["not_attempted_due_to_stop"],
            "provider_rate_limit_errors": details["provider_rate_limit_errors"],
            "written": str(destination.relative_to(Path.cwd().resolve())),
            "report": str(report_path.relative_to(Path.cwd().resolve())),
            "live_execution_enabled": False,
        }
    else:
        raise ValueError("Unsupported stock_data_cli command")
    return report


def register_stock_split_reconcile(sub) -> None:
    split_audit = sub.add_parser(
        "stock-split-reconcile",
        help="Audit SEC-verified reverse splits; optionally create a separate adjusted research CSV",
    )
    split_audit.add_argument("--prices", type=Path, required=True)
    split_audit.add_argument("--manifest", type=Path, required=True)
    split_audit.add_argument("--apply-confirmed", action="store_true")
    split_audit.add_argument("--output", type=Path, default=None)
    split_audit.add_argument("--report", type=Path,
                             default=Path("local_data/split_reconciliation_report.json"))


def register_stock_universe(sub) -> None:
    market_universe = sub.add_parser(
        "stock-universe",
        help="Discover current all-US-exchange-listed equity candidates from Nasdaq Trader",
    )
    market_universe.add_argument(
        "--output", type=Path, default=Path("local_data/us_equity_universe.csv")
    )
    market_universe.add_argument(
        "--report", type=Path, default=Path("local_data/us_equity_universe_report.json")
    )
    market_universe.add_argument("--nasdaq-file", type=Path, default=None)
    market_universe.add_argument("--other-file", type=Path, default=None)


def register_stock_market_audit(sub) -> None:
    market_audit = sub.add_parser(
        "stock-market-audit",
        help="Audit whole-US-stock quote CSV against current listings and real NYSE sessions",
    )
    market_audit.add_argument("--universe", type=Path, required=True)
    market_audit.add_argument("--prices", type=Path, required=True)
    market_audit.add_argument("--as-of", required=True, help="Actual NYSE session YYYY-MM-DD")
    market_audit.add_argument("--min-history-sessions", type=int, default=253)
    market_audit.add_argument("--recent-sessions", type=int, default=63)
    market_audit.add_argument("--min-recent-coverage-pct", type=float, default=85)
    market_audit.add_argument("--min-month-end-coverage-pct", type=float, default=90)
    market_audit.add_argument("--min-eligible-coverage-pct", type=float, default=75)
    market_audit.add_argument("--min-eligible-names", type=int, default=500)
    market_audit.add_argument("--max-manifest-age-days", type=int, default=7)
    market_audit.add_argument("--output", type=Path,
                              default=Path("local_data/us_market_data_audit.json"))


def register_stock_sync_market(sub) -> None:
    market_sync = sub.add_parser(
        "stock-sync-market",
        help="Resume-safe quote download for every symbol in a market universe",
    )
    market_sync.add_argument("--universe", type=Path, required=True)
    market_sync.add_argument("--start", default="2024-01-01")
    market_sync.add_argument("--end", default="2026-11-21")
    market_sync.add_argument(
        "--cache-dir", type=Path, default=Path("local_data/us_equity_cache")
    )
    market_sync.add_argument(
        "--output", type=Path, default=Path("local_data/us_equity_prices.csv")
    )
    market_sync.add_argument(
        "--report", type=Path, default=Path("local_data/us_equity_coverage.json")
    )
    market_sync.add_argument(
        "--max-symbols", type=int, default=0,
        help="0=all uncovered symbols; positive integer=quick trial batch",
    )
    market_sync.add_argument("--workers", type=int, default=2)
    market_sync.add_argument("--attempts", type=int, default=2)
    market_sync.add_argument("--batch-size", type=int, default=25)
    market_sync.add_argument("--batch-pause-seconds", type=float, default=3.0)
    market_sync.add_argument("--max-consecutive-failures", type=int, default=12)
    market_sync.add_argument("--max-rate-limit-errors", type=int, default=2)
    market_sync.add_argument("--cache-only", action="store_true",
                             help="Rebuild price CSV + coverage report using existing cache only; no provider calls")

