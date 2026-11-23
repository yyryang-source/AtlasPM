"""Research-only stock selection CLI commands.

The public CLI command names and argument defaults are kept stable.
"""

from __future__ import annotations

import json
from pathlib import Path

from atlaspm.quant.ibkr_gateway import require_private_output
from atlaspm.selection.stock_screener import screen_stocks
from atlaspm.selection.stock_walkforward import backtest_stock_screener
from atlaspm.selection.us_market_universe import build_us_listed_universe
from atlaspm.selection.us_market_prices import sync_us_market_prices
from atlaspm.selection.us_market_quality import audit_current_us_stock_prices
from atlaspm.selection.ai_stock_ranker import train_ai_stock_ranker, rank_with_ai
from atlaspm.selection.ai_stock_ranker_v2 import train_stock_learning_to_rank, rank_stocks_v2
from atlaspm.selection.v2_momentum_diagnostics import diagnose_v2_momentum_extremes
from atlaspm.selection.corporate_action_reconciliation import reconcile_reverse_splits
from atlaspm.selection.consensus_20_screener import run_20_signal_research
from atlaspm.selection.monthly_stock_selection import monthly_candidates, write_monthly_research
from atlaspm.selection.alpha_eligibility import build_from_file, research_action_check, write_eligibility_json


COMMANDS = frozenset({
    "stock-alpha-gate",
    "stock-alpha-check",
    "stock-ai-monthly",
    "stock-consensus-20",
    "stock-ai-train-v2",
    "stock-ai-diagnose-momentum",
    "stock-split-reconcile",
    "stock-ai-rank-v2",
    "stock-ai-train",
    "stock-ai-rank",
    "stock-universe",
    "stock-market-audit",
    "stock-sync-market",
    "stock-backtest",
    "stock-screen",
})


def run(args) -> dict:
    if args.command == "stock-alpha-gate":
        source = require_private_output(args.monthly_report)
        destination = require_private_output(args.output)
        if source.suffix.lower() != ".json" or destination.suffix.lower() != ".json":
            raise ValueError("Alpha eligibility input/output must be .json under local_data")
        if source == destination:
            raise ValueError("Alpha gate cannot overwrite monthly input report")
        candidate_gate = build_from_file(
            source, top_percent=args.top_percent,
            max_ranked_names=args.max_ranked_names,
            minimum_scored_names=args.minimum_scored_names,
            minimum_listing_coverage_pct=args.minimum_listing_coverage_pct,
            maximum_model_age_days=args.maximum_model_age_days,
        )
        written = write_eligibility_json(
            candidate_gate, destination, overwrite=args.overwrite
        )
        report = {
            "month": candidate_gate["month"],
            "valid_for_month": candidate_gate["valid_for_month"],
            "eligibility_status": candidate_gate["research_eligibility_status"],
            "eligible_count": candidate_gate["eligible_count"],
            "blocked_reasons": candidate_gate["blocked_reasons"],
            "eligible_symbols": candidate_gate["eligible_symbols"],
            "written": written,
            "live_execution_enabled": False,
            "automated_handoff_approved": False,
        }
    elif args.command == "stock-alpha-check":
        source = require_private_output(args.gate)
        if source.suffix.lower() != ".json":
            raise ValueError("Alpha gate must be private .json")
        candidate_gate = json.loads(source.read_text(encoding="utf-8"))
        report = research_action_check(
            candidate_gate, symbol=args.symbol, action=args.action,
            decision_date=args.decision_date,
        )
    elif args.command == "stock-ai-monthly":
        output_probe = require_private_output(args.output_dir / "_output_probe.json")
        model_path = require_private_output(args.model)
        output_dir = output_probe.parent
        source_paths = {
            args.prices.expanduser().resolve(),
            args.benchmark_prices.expanduser().resolve(),
            model_path,
            model_path.with_suffix(model_path.suffix + ".meta.json"),
        }
        if args.universe is not None:
            source_paths.add(args.universe.expanduser().resolve())
        if any(p == output_dir or p.is_relative_to(output_dir)
               for p in source_paths):
            raise ValueError("Monthly output directory must not contain input/model files")
        result = monthly_candidates(
            args.prices, args.benchmark_prices, model_path,
            month=args.month, benchmark_symbol=args.benchmark_symbol,
            universe_path=args.universe, min_scored=args.min_scored,
            min_market_coverage_pct=args.min_market_coverage_pct,
            max_training_age_days=args.max_training_age_days,
        )
        artifacts = write_monthly_research(
            result, output_dir, overwrite=args.overwrite
        )
        report = {
            "month": result["month"],
            "as_of_nyse_session": result["as_of_nyse_session"],
            "report_status": result["report_status"],
            "review_reasons": result["review_reasons"],
            "data_quality": result["data_quality"],
            "top20_returned": result["top_20"]["returned"],
            "top50_returned": result["top_50"]["returned"],
            "top20_ambiguous": result["top_20"]["boundary_tied"],
            "top50_ambiguous": result["top_50"]["boundary_tied"],
            "artifacts": artifacts,
            "research_only": True,
            "live_execution_enabled": False,
        }
    elif args.command == "stock-consensus-20":
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".json":
            raise ValueError("20-signal research output must be a private JSON file")
        inputs = {args.prices.expanduser().resolve()}
        if args.universe is not None:
            inputs.add(args.universe.expanduser().resolve())
        if destination in inputs:
            raise ValueError("Consensus report cannot overwrite input files")
        result = run_20_signal_research(
            args.prices, as_of=args.as_of,
            top=args.top, min_price=args.min_price,
            max_daily_ratio=args.max_daily_ratio,
            universe_path=args.universe,
            min_screenable=args.min_screenable,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "as_of": result["as_of"],
            "status": result["status"],
            "signal_count": result["n_signals"],
            "family_count": result["n_families"],
            "screenable_stocks": result["data_quality"]["eligible_price_only_names"],
            "excluded_counts": result["data_quality"]["excluded_counts"],
            "selected": result["selected"],
            "preview_not_trade_instructions": result["preview_not_trade_instructions"][:10],
            "review_reasons": result["review_reasons"],
            "written": str(destination.relative_to(Path.cwd().resolve())),
            "research_only": True,
            "live_execution_enabled": False,
            "automated_handoff_approved": False,
        }
    elif args.command == "stock-ai-train-v2":
        destination = require_private_output(args.model_output)
        report_path = require_private_output(args.report)
        if destination.suffix.lower() != ".txt" or report_path.suffix.lower() != ".json":
            raise ValueError("V2 ranker model must be private .txt and report private .json")
        metadata = destination.with_suffix(destination.suffix + ".meta.json")
        inputs = {args.prices.expanduser().resolve(), args.benchmark_prices.expanduser().resolve()}
        if len({destination, metadata, report_path}) != 3 or any(
            item in inputs for item in (destination, metadata, report_path)
        ):
            raise ValueError("Ranker outputs must not overwrite model metadata or input data")
        result = train_stock_learning_to_rank(
            args.prices, args.benchmark_prices, destination,
            start=args.start, train_end=args.train_end,
            validation_end=args.validation_end, test_end=args.test_end,
            benchmark_symbol=args.benchmark_symbol,
            horizon_sessions=args.horizon_sessions,
            sample_every_sessions=args.sample_every_sessions,
            num_boost_round=args.num_boost_round, top=args.top,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "status": result["status"],
            "model": str(destination.relative_to(Path.cwd().resolve())),
            "report": str(report_path.relative_to(Path.cwd().resolve())),
            "split_counts": result["split_counts"],
            "group_counts": result["group_counts"],
            "validation": result["validation"],
            "test": result["test"],
            "model_research_review": result["model_research_review"],
            "live_execution_enabled": False,
        }
    elif args.command == "stock-ai-diagnose-momentum":
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".json":
            raise ValueError("Momentum diagnostics output must be private JSON")
        inputs = {
            args.prices.expanduser().resolve(),
            args.benchmark_prices.expanduser().resolve(),
        }
        if destination in inputs:
            raise ValueError("Diagnostics output must not overwrite source CSV files")
        result = diagnose_v2_momentum_extremes(
            args.prices, args.benchmark_prices,
            start=args.start, train_end=args.train_end,
            validation_end=args.validation_end, test_end=args.test_end,
            benchmark_symbol=args.benchmark_symbol,
            horizon_sessions=args.horizon_sessions,
            sample_every_sessions=args.sample_every_sessions,
            top=args.top,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "status": result["status"],
            "validation": {
                key: value for key, value in result["validation"].items()
                if key != "cohorts"
            },
            "test": {
                key: value for key, value in result["test"].items()
                if key != "cohorts"
            },
            "written": str(destination.relative_to(Path.cwd().resolve())),
            "live_execution_enabled": False,
        }
    elif args.command == "stock-split-reconcile":
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
    elif args.command == "stock-ai-rank-v2":
        destination = require_private_output(args.output)
        model_path = require_private_output(args.model)
        if destination.suffix.lower() != ".json":
            raise ValueError("Ranker V2 output must be private .json")
        if destination in {
            args.prices.expanduser().resolve(), args.benchmark_prices.expanduser().resolve(),
            model_path, model_path.with_suffix(model_path.suffix + ".meta.json"),
        }:
            raise ValueError("Ranker output cannot overwrite input data")
        result = rank_stocks_v2(
            args.prices, args.benchmark_prices, model_path,
            as_of=args.as_of, top=args.top, benchmark_symbol=args.benchmark_symbol,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "as_of": result["as_of"],
            "scored_count": result["scored_count"],
            "selected": result["selected"],
            "ranking_diagnostics": result["ranking_diagnostics"],
            "model_diagnostics": result["model_diagnostics"],
            "written": str(destination.relative_to(Path.cwd().resolve())),
            "live_execution_enabled": False,
        }
    elif args.command == "stock-ai-train":
        model_path = require_private_output(args.model_output)
        report_path = require_private_output(args.report)
        if model_path.suffix.lower() != ".txt" or report_path.suffix.lower() != ".json":
            raise ValueError("AI model needs private local_data .txt and report .json")
        inputs = {args.prices.expanduser().resolve(),
                  args.benchmark_prices.expanduser().resolve()}
        metadata_path = model_path.with_suffix(model_path.suffix + ".meta.json")
        if (len({model_path, metadata_path, report_path}) != 3
                or any(p in inputs for p in (model_path, metadata_path, report_path))):
            raise ValueError("Model and report must be distinct, cannot overwrite input data")
        result = train_ai_stock_ranker(
            args.prices, args.benchmark_prices, model_path,
            start=args.start, train_end=args.train_end,
            validation_end=args.validation_end, test_end=args.test_end,
            benchmark_symbol=args.benchmark_symbol,
            sample_every_sessions=args.sample_every_sessions,
            horizon_sessions=args.horizon_sessions,
            num_boost_round=args.num_boost_round,
            min_train_rows=args.min_train_rows,
            top=args.top,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "status": result["status"],
            "model": str(model_path.relative_to(Path.cwd().resolve())),
            "report": str(report_path.relative_to(Path.cwd().resolve())),
            "split_counts": result["split_counts"],
            "purged_rows": result["purged_rows"],
            "validation": result["validation"],
            "test": result["test"],
            "live_execution_enabled": False,
        }
    elif args.command == "stock-ai-rank":
        destination = require_private_output(args.output)
        model_path = require_private_output(args.model)
        if destination.suffix.lower() != ".json":
            raise ValueError("AI rank output must be private local_data .json")
        inputs = {args.prices.expanduser().resolve(),
                  args.benchmark_prices.expanduser().resolve(),
                  model_path,
                  model_path.with_suffix(model_path.suffix + ".meta.json")}
        if destination in inputs:
            raise ValueError("AI rank output cannot overwrite inputs or model")
        result = rank_with_ai(
            args.prices, args.benchmark_prices, model_path,
            as_of=args.as_of, top=args.top, benchmark_symbol=args.benchmark_symbol,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "as_of": result["as_of"],
            "universe_count": result["universe_count"],
            "scored_count": result["scored_count"],
            "excluded_count": result["excluded_count"],
            "selected": result["selected"],
            "ranking_diagnostics": result["ranking_diagnostics"],
            "model_diagnostics": result["model_diagnostics"],
            "written": str(destination.relative_to(Path.cwd().resolve())),
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
    elif args.command == "stock-backtest":
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".json":
            raise ValueError("Stock backtest output must be private local_data .json")
        sources = {args.prices.expanduser().resolve(),
                   args.benchmark_prices.expanduser().resolve()}
        if destination in sources:
            raise ValueError("Stock backtest cannot overwrite its inputs")
        result = backtest_stock_screener(
            args.prices, args.benchmark_prices,
            start=args.start, end=args.end,
            benchmark_symbol=args.benchmark_symbol,
            top=args.top, rebalance_days=args.rebalance_days,
            fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
            max_volatility=args.max_volatility,
            max_drawdown=args.max_drawdown,
            require_uptrend=args.require_uptrend,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "written": "local_data/" + destination.relative_to(
                (Path.cwd().resolve() / "local_data").resolve()
            ).as_posix(),
            "requested_window": result["requested_window"],
            "actual_window": result["actual_window"],
            "universe_count": result["universe_count_excluding_benchmark"],
            "decision_count": result["decision_count"],
            "executed_rebalances": result["executed_rebalances"],
            "portfolio": result["portfolio"],
            "benchmark": result["benchmark"],
            "equal_weight_universe": result["equal_weight_universe"],
            "relative": result["relative"],
            "live_execution_enabled": False,
            "data_caution": "Surviving ticker universe; NOT survivorship-safe or certified alpha.",
        }
    elif args.command == "stock-screen":
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".json":
            raise ValueError("Stock screen output must be a private local_data .json file")
        inputs = {args.prices.expanduser().resolve()}
        if args.fundamentals:
            inputs.add(args.fundamentals.expanduser().resolve())
        if destination in inputs:
            raise ValueError("Refusing to overwrite stock screener input")
        result = screen_stocks(
            args.prices,
            args.as_of,
            fundamentals_path=args.fundamentals,
            top=args.top,
            max_price_age_days=args.max_price_age_days,
            max_fundamental_age_days=args.max_fundamental_age_days,
            max_annual_volatility=args.max_volatility,
            max_drawdown=args.max_drawdown,
            minimum_market_cap_usd=args.min_market_cap,
            require_uptrend=args.require_uptrend,
            max_per_sector=args.max_per_sector,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "written": "local_data/" + destination.relative_to(
                (Path.cwd().resolve() / "local_data").resolve()
            ).as_posix(),
            "as_of": result["as_of"],
            "mode": result["mode"],
            "universe_count": result["universe_count"],
            "eligible_count": result["eligible_count"],
            "excluded_count": len(result["excluded"]),
            "selected": [
                {"rank": row["rank"], "symbol": row["symbol"], "score": row["score"]}
                for row in result["selected"]
            ],
            "live_execution_enabled": False,
        }
    return report


def register_parsers(sub) -> None:
    alpha_gate = sub.add_parser(
        "stock-alpha-gate",
        help="Build rank-percentile allowlist for monthly quant research, never order permission",
    )
    alpha_gate.add_argument("--monthly-report", type=Path, required=True)
    alpha_gate.add_argument("--top-percent", type=float, default=10.0)
    alpha_gate.add_argument("--max-ranked-names", type=int, default=50)
    alpha_gate.add_argument("--minimum-scored-names", type=int, default=500)
    alpha_gate.add_argument("--minimum-listing-coverage-pct", type=float, default=90)
    alpha_gate.add_argument("--maximum-model-age-days", type=int, default=180)
    alpha_gate.add_argument("--output", type=Path, required=True)
    alpha_gate.add_argument("--overwrite", action="store_true")

    alpha_check = sub.add_parser(
        "stock-alpha-check",
        help="Check hypothetical NEW/INCREASE or REDUCE/CLOSE against alpha universe",
    )
    alpha_check.add_argument("--gate", type=Path, required=True)
    alpha_check.add_argument("--symbol", required=True)
    alpha_check.add_argument("--action", required=True)
    alpha_check.add_argument("--decision-date", required=True)

    monthly = sub.add_parser(
        "stock-ai-monthly",
        help="Produce calendar-correct NYSE month-end pure AI stock research; no orders",
    )
    monthly.add_argument("--prices", type=Path, required=True)
    monthly.add_argument("--benchmark-prices", type=Path, required=True)
    monthly.add_argument("--model", type=Path, required=True)
    monthly.add_argument("--month", required=True, help="Calendar month YYYY-MM")
    monthly.add_argument("--universe", type=Path, default=None,
                         help="Optional listing manifest for real full-market coverage audit")
    monthly.add_argument("--benchmark-symbol", default="SPY")
    monthly.add_argument("--min-scored", type=int, default=20)
    monthly.add_argument("--min-market-coverage-pct", type=float, default=90)
    monthly.add_argument("--max-training-age-days", type=int, default=365)
    monthly.add_argument("--output-dir", type=Path,
                         default=Path("local_data/monthly_stock_research"))
    monthly.add_argument("--overwrite", action="store_true",
                         help="Explicitly replace previous month's research snapshot")

    ensemble = sub.add_parser(
        "stock-consensus-20",
        help="Offline 20 original price signal screener in five factor families; research only",
    )
    ensemble.add_argument("--prices", type=Path, required=True)
    ensemble.add_argument("--universe", type=Path, default=None)
    ensemble.add_argument("--as-of", required=True)
    ensemble.add_argument("--top", type=int, default=10)
    ensemble.add_argument("--min-price", type=float, default=5.0)
    ensemble.add_argument("--max-daily-ratio", type=float, default=4.0)
    ensemble.add_argument("--min-screenable", type=int, default=100)
    ensemble.add_argument("--output", type=Path,
                          default=Path("local_data/consensus_20_research.json"))

    ai_train_v2 = sub.add_parser(
        "stock-ai-train-v2",
        help="Train a pure stock learning-to-rank LightGBM rank_xendcg model",
    )
    ai_train_v2.add_argument("--prices", type=Path, required=True)
    ai_train_v2.add_argument("--benchmark-prices", type=Path, required=True)
    ai_train_v2.add_argument("--start", default="2000-01-01")
    ai_train_v2.add_argument("--train-end", default="2018-12-31")
    ai_train_v2.add_argument("--validation-end", default="2022-12-30")
    ai_train_v2.add_argument("--test-end", default="2026-11-20")
    ai_train_v2.add_argument("--benchmark-symbol", default="SPY")
    ai_train_v2.add_argument("--horizon-sessions", type=int, default=21)
    ai_train_v2.add_argument("--sample-every-sessions", type=int, default=21)
    ai_train_v2.add_argument("--num-boost-round", type=int, default=300)
    ai_train_v2.add_argument("--top", type=int, default=10)
    ai_train_v2.add_argument("--model-output", type=Path,
                             default=Path("local_data/atlas_stock_ranker_v2.txt"))
    ai_train_v2.add_argument("--report", type=Path,
                             default=Path("local_data/atlas_stock_ranker_v2_report.json"))

    diagnostic = sub.add_parser(
        "stock-ai-diagnose-momentum",
        help="Investigate per-date extreme 12-1 momentum Top N failures; no model fitting",
    )
    diagnostic.add_argument("--prices", type=Path, required=True)
    diagnostic.add_argument("--benchmark-prices", type=Path, required=True)
    diagnostic.add_argument("--start", default="2025-01-02")
    diagnostic.add_argument("--train-end", default="2025-12-31")
    diagnostic.add_argument("--validation-end", default="2026-05-29")
    diagnostic.add_argument("--test-end", default="2026-11-20")
    diagnostic.add_argument("--benchmark-symbol", default="SPY")
    diagnostic.add_argument("--horizon-sessions", type=int, default=21)
    diagnostic.add_argument("--sample-every-sessions", type=int, default=21)
    diagnostic.add_argument("--top", type=int, default=10)
    diagnostic.add_argument("--output", type=Path,
                            default=Path("local_data/v2_momentum_diagnostics.json"))

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

    ai_rank_v2 = sub.add_parser(
        "stock-ai-rank-v2",
        help="Rank stocks with an ordinal learning-to-rank model, no orders",
    )
    ai_rank_v2.add_argument("--prices", type=Path, required=True)
    ai_rank_v2.add_argument("--benchmark-prices", type=Path, required=True)
    ai_rank_v2.add_argument("--model", type=Path, required=True)
    ai_rank_v2.add_argument("--as-of", required=True)
    ai_rank_v2.add_argument("--top", type=int, default=20)
    ai_rank_v2.add_argument("--benchmark-symbol", default="SPY")
    ai_rank_v2.add_argument("--output", type=Path,
                            default=Path("local_data/atlas_stock_ranker_v2_rankings.json"))

    ai_train = sub.add_parser(
        "stock-ai-train",
        help="Train a price-based LightGBM alpha ranker on purged chronological market splits",
    )
    ai_train.add_argument("--prices", type=Path, required=True)
    ai_train.add_argument("--benchmark-prices", type=Path, required=True)
    ai_train.add_argument("--start", default="2000-01-01")
    ai_train.add_argument("--train-end", default="2018-12-31")
    ai_train.add_argument("--validation-end", default="2022-12-30")
    ai_train.add_argument("--test-end", default="2026-11-20")
    ai_train.add_argument("--benchmark-symbol", default="SPY")
    ai_train.add_argument("--sample-every-sessions", type=int, default=21)
    ai_train.add_argument("--horizon-sessions", type=int, default=21)
    ai_train.add_argument("--num-boost-round", type=int, default=180)
    ai_train.add_argument("--min-train-rows", type=int, default=300)
    ai_train.add_argument("--top", type=int, default=10)
    ai_train.add_argument("--model-output", type=Path,
                          default=Path("local_data/atlas_ai_stock_lgbm.txt"))
    ai_train.add_argument("--report", type=Path,
                          default=Path("local_data/atlas_ai_stock_train.json"))

    ai_rank = sub.add_parser(
        "stock-ai-rank",
        help="Rank stocks with a locally trained LightGBM alpha ranker; no trades",
    )
    ai_rank.add_argument("--prices", type=Path, required=True)
    ai_rank.add_argument("--benchmark-prices", type=Path, required=True)
    ai_rank.add_argument("--model", type=Path, required=True)
    ai_rank.add_argument("--benchmark-symbol", default="SPY")
    ai_rank.add_argument("--as-of", required=True)
    ai_rank.add_argument("--top", type=int, default=20)
    ai_rank.add_argument("--output", type=Path,
                         default=Path("local_data/atlas_ai_stock_ranking.json"))

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

    stock_backtest = sub.add_parser(
        "stock-backtest",
        help="Long-only 2000-2026 walk-forward stock ranking vs adjusted SPY, no orders",
    )
    stock_backtest.add_argument("--prices", type=Path, required=True)
    stock_backtest.add_argument("--benchmark-prices", type=Path, required=True)
    stock_backtest.add_argument("--benchmark-symbol", default="SPY")
    stock_backtest.add_argument("--start", default="2000-01-01")
    stock_backtest.add_argument("--end", default="2026-11-20")
    stock_backtest.add_argument("--top", type=int, default=10)
    stock_backtest.add_argument("--rebalance-days", type=int, default=21)
    stock_backtest.add_argument("--fee-bps", type=float, default=5.0)
    stock_backtest.add_argument("--slippage-bps", type=float, default=5.0)
    stock_backtest.add_argument("--max-volatility", type=float, default=0.80)
    stock_backtest.add_argument("--max-drawdown", type=float, default=0.65)
    stock_backtest.add_argument("--require-uptrend", action="store_true")
    stock_backtest.add_argument(
        "--output", type=Path,
        default=Path("local_data/stock_walkforward_2000_2026.json"),
    )

    stock = sub.add_parser(
        "stock-screen",
        help="Offline point-in-time-capped multi-factor stock ranking; no orders",
    )
    stock.add_argument("--prices", type=Path, required=True,
                       help="Long CSV: date,symbol,adjusted_close")
    stock.add_argument("--as-of", required=True, help="Evaluation date YYYY-MM-DD")
    stock.add_argument("--fundamentals", type=Path, default=None,
                       help="Optional dated fundamental snapshot CSV")
    stock.add_argument("--top", type=int, default=10)
    stock.add_argument("--max-price-age-days", type=int, default=10)
    stock.add_argument("--max-fundamental-age-days", type=int, default=200)
    stock.add_argument("--max-volatility", type=float, default=0.80)
    stock.add_argument("--max-drawdown", type=float, default=0.65)
    stock.add_argument("--min-market-cap", type=float, default=0.0,
                       help="Minimum market_cap_usd; requires --fundamentals")
    stock.add_argument("--require-uptrend", action="store_true")
    stock.add_argument("--max-per-sector", type=int, default=None,
                       help="Max picks per sector; requires --fundamentals")
    stock.add_argument("--output", type=Path,
                       default=Path("local_data/stock_screen.json"))

