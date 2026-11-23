"""stock_monthly_cli: offline AtlasPM command handlers; no live orders.

Focused CLI adapter, not the underlying quantitative strategy/model.
"""

from __future__ import annotations

import json
from pathlib import Path
from atlaspm.execution.ibkr_gateway import require_private_output
from atlaspm.strategies.equity.monthly_stock_selection import monthly_candidates, write_monthly_research
from atlaspm.strategies.equity.alpha_eligibility import build_from_file, research_action_check, write_eligibility_json

COMMANDS = frozenset(["stock-alpha-gate","stock-alpha-check","stock-ai-monthly"])


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
    else:
        raise ValueError("Unsupported stock_monthly_cli command")
    return report


def register_stock_alpha_gate(sub) -> None:
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


def register_stock_alpha_check(sub) -> None:
    alpha_check = sub.add_parser(
        "stock-alpha-check",
        help="Check hypothetical NEW/INCREASE or REDUCE/CLOSE against alpha universe",
    )
    alpha_check.add_argument("--gate", type=Path, required=True)
    alpha_check.add_argument("--symbol", required=True)
    alpha_check.add_argument("--action", required=True)
    alpha_check.add_argument("--decision-date", required=True)


def register_stock_ai_monthly(sub) -> None:
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

