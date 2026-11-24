"""Pure monthly AI stock candidate-selection orchestration.

Calendar-correct NYSE month end, 17:00 America/New_York completion gate,
missing/stale quote coverage checks, tie-aware Top20/50 and model-age review.
Produces research JSON/CSV only. NEVER calls brokers, creates orders, determines
weights, manages leverage, or mutates the existing AtlasPM quant/risk stack.
"""

from __future__ import annotations

import calendar
import csv
from datetime import date, datetime, time
import json
import os
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from atlaspm.research.ai_stock_ranker_v2 import rank_stocks_v2
from atlaspm.data.us_market_universe import read_market_universe

_MARKET_TZ = ZoneInfo("America/New_York")
_MONTH_PATTERN = re.compile(r"^(20\d{2})-(0[1-9]|1[0-2])$")
SCHEMA = "atlaspm_monthly_stock_candidates_v1"


def month_end_nyse_session(month: str) -> date:
    """The actual NYSE final session; weekends and holidays are not assumed."""
    match = _MONTH_PATTERN.fullmatch(month)
    if match is None:
        raise ValueError("month must be YYYY-MM (2000..2099)")
    year, month_num = int(match.group(1)), int(match.group(2))
    try:
        import pandas_market_calendars as mcal
    except ImportError as exc:
        raise ValueError(
            "Install optional NYSE calendar: python -m pip install -e '.[calendar]'"
        ) from exc
    start = date(year, month_num, 1)
    end = date(year, month_num, calendar.monthrange(year, month_num)[1])
    nyse = mcal.get_calendar("NYSE")
    sessions = nyse.valid_days(start_date=start.isoformat(), end_date=end.isoformat())
    if len(sessions) == 0:
        raise ValueError(f"No NYSE sessions reported for {month}")
    return sessions[-1].date()


def monthly_candidates(
    prices_path: Path,
    benchmark_path: Path,
    model_path: Path,
    *,
    month: str,
    benchmark_symbol: str = "SPY",
    universe_path: Path | None = None,
    min_scored: int = 20,
    min_market_coverage_pct: float = 90.0,
    max_training_age_days: int = 365,
    as_at: datetime | None = None,
) -> dict[str, Any]:
    """Month-end-only selection with explicit research readiness/review gates.

    as_at exists for deterministic offline tests; CLI uses the actual clock.
    """
    if isinstance(min_scored, bool) or not 1 <= min_scored <= 100000:
        raise ValueError("min-scored must be an integer from 1 to 100000")
    if not 0 <= min_market_coverage_pct <= 100:
        raise ValueError("min-market-coverage-pct must be in 0..100")
    if isinstance(max_training_age_days, bool) or not 0 <= max_training_age_days <= 20000:
        raise ValueError("max-training-age-days must be in 0..20000")
    last_session = month_end_nyse_session(month)
    now = as_at or datetime.now(_MARKET_TZ)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("as_at must have a timezone")
    now = now.astimezone(_MARKET_TZ)
    last_close_buffer = datetime.combine(last_session, time(hour=17), tzinfo=_MARKET_TZ)
    if now < last_close_buffer:
        raise ValueError(
            f"Month {month} is not complete. NYSE final session "
            f"{last_session.isoformat()} must have finished and settled past 17:00 ET."
        )

    # The ranker refuses a benchmark without this EXACT close and excludes
    # any stale stocks which do not have a close on the same session date.
    rank = rank_stocks_v2(
        prices_path, benchmark_path, model_path,
        as_of=last_session.isoformat(), top=50,
        benchmark_symbol=benchmark_symbol,
    )
    scored = rank["scored_count"]
    model_metadata = json.loads(
        model_path.with_suffix(model_path.suffix + ".meta.json").read_text(encoding="utf-8")
    )
    train_date = date.fromisoformat(model_metadata["train_end"])
    training_age_days = (last_session - train_date).days
    if training_age_days < 0:
        raise ValueError("Model was trained using data beyond requested month-end")

    full_market_count = None
    market_coverage_pct = None
    outside_catalog = 0
    if universe_path is not None:
        catalog = read_market_universe(universe_path)
        full_market_count = len(catalog)
        catalog_symbols = {r["provider_symbol"] for r in catalog}
        scored_symbols = set(rank["scored_symbols"])
        # Only ACTUAL scored ticker identities matching this snapshot count.
        # A file of unrelated symbols must not impersonate full-US coverage.
        outside_catalog = len(scored_symbols - catalog_symbols)
        matched = len(scored_symbols & catalog_symbols)
        market_coverage_pct = round(100 * matched / full_market_count, 2)
    issues: list[str] = []
    if outside_catalog:
        issues.append(
            f"SYMBOLS_OUTSIDE_MANIFEST: {outside_catalog} scored quote tickers "
            "are absent from listing snapshot; reconcile symbol mappings"
        )
    if training_age_days > max_training_age_days:
        issues.append(
            f"STALE_MODEL: trained through {train_date.isoformat()}, "
            f"{training_age_days} calendar days before month-end"
        )
    if scored < min_scored:
        issues.append(f"INSUFFICIENT_SCORED_STOCKS: {scored} < {min_scored}")
    if full_market_count is None:
        issues.append(
            "UNVERIFIED_FULL_US_MARKET_SCOPE: no current listing manifest provided"
        )
    elif market_coverage_pct < min_market_coverage_pct:
        issues.append(
            f"INSUFFICIENT_MARKET_COVERAGE: {market_coverage_pct}% "
            f"< {min_market_coverage_pct}%"
        )

    selected_20 = rank["selected"][:20]
    selected_50 = rank["selected"][:50]
    def splits_tie(selection: list[dict[str, Any]], requested_n: int) -> bool:
        if len(selection) < requested_n:
            return False
        last = selection[requested_n - 1]
        return last["rank"] + last["tie_count"] - 1 > requested_n

    if splits_tie(selected_20, 20):
        issues.append("TOP20_AMBIGUOUS: boundary splits an equal-score group")
    if splits_tie(selected_50, 50):
        issues.append("TOP50_AMBIGUOUS: boundary splits an equal-score group")
    if scored < 50:
        issues.append(f"TOP50_INCOMPLETE: only {scored} stocks can be scored")

    # Hard line: a candidate ranking can be researched, but NEVER signals
    # permission for any live execution or automated position sizing.
    output = {
        "schema": SCHEMA,
        "month": month,
        "as_of_nyse_session": last_session.isoformat(),
        "next_calendar_month": (
            date(last_session.year + (last_session.month == 12),
                 last_session.month % 12 + 1, 1).strftime("%Y-%m")
        ),
        "created_at_et": now.isoformat(timespec="seconds"),
        "selection_policy": "last_completed_NYSE_session_of_calendar_month_after_17_ET",
        "refresh_policy": {
            "market_data": "daily_after_close_externally_refreshed_input_required",
            "stock_rankings": "once_per_completed_calendar_month",
            "model_retraining": "quarterly_review_manual_not_automatic",
            "holding_horizon_target_sessions": rank["forecast_horizon_sessions"],
        },
        "data_quality": {
            "provided_price_symbols": rank["candidate_universe_count"],
            "scored_at_month_end": scored,
            "excluded_for_missing_or_insufficient_history": rank["excluded_count"],
            "listing_manifest_count": full_market_count,
            "listed_market_coverage_pct": market_coverage_pct,
            "scored_symbols_outside_manifest": outside_catalog,
            "scope": "provided_price_file_only" if full_market_count is None
                     else "current_US_exchange_listed_manifest",
        },
        "model": {
            "kind": rank["method"],
            "sha256": rank["model_sha256"],
            "trained_through": train_date.isoformat(),
            "training_age_days": training_age_days,
            "best_iteration": rank["model_diagnostics"]["best_iteration"],
            "score_semantics": "ordinal_relative_rank_utility_not_return",
        },
        "ranking_diagnostics": rank["ranking_diagnostics"],
        "top_20": {
            "requested": 20,
            "returned": len(selected_20),
            "boundary_tied": splits_tie(selected_20, 20),
            "candidates": selected_20,
        },
        "top_50": {
            "requested": 50,
            "returned": len(selected_50),
            "complete": len(selected_50) == 50,
            "boundary_tied": splits_tie(selected_50, 50),
            "candidates": selected_50,
        },
        "report_status": "REVIEW_REQUIRED" if issues else "READY_FOR_RESEARCH_REVIEW",
        "review_reasons": issues,
        "research_only": True,
        "automated_handoff_approved": False,
        "live_execution_enabled": False,
        "order_instructions": [],
        "warnings": [
            "This is a pure research ranking, never a trade plan, target weight, or order.",
            "Missing month-end quotes are excluded; market-wide coverage requires a listing manifest.",
            "A quarterly retraining policy is guidance, NOT implemented automatic retraining.",
            "Today's listing manifest omits delisted historical securities.",
            "The V2 model was tested on 38 surviving stocks, not validated on all US equities.",
            "Check source timezone and market vendor settlement before consuming data.",
        ],
    }
    return output


def write_monthly_research(
    result: dict[str, Any],
    output_dir: Path,
    *,
    overwrite: bool = False,
) -> dict[str, str]:
    """Atomically write a dated JSON report + CSV candidate list; no orders."""
    if result.get("schema") != SCHEMA or result.get("live_execution_enabled") is not False:
        raise ValueError("Not an approved stock-only monthly report schema")
    month = result["month"]
    if _MONTH_PATTERN.fullmatch(month) is None:
        raise ValueError("Invalid report month")
    out_json = output_dir / f"{month}_monthly_stock_research.json"
    out_csv = output_dir / f"{month}_monthly_top50.csv"
    if not overwrite and (out_json.exists() or out_csv.exists()):
        raise ValueError(
            "Monthly report exists; refusing to overwrite. "
            "Use --overwrite only when intentionally replacing a research snapshot."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_json = out_json.with_suffix(out_json.suffix + ".part")
    tmp_csv = out_csv.with_suffix(out_csv.suffix + ".part")
    try:
        tmp_json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        with tmp_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "month", "as_of_date", "symbol", "rank", "display_position",
                "ranking_score", "tie_count", "report_status", "research_only",
            ])
            for row in result["top_50"]["candidates"]:
                writer.writerow([
                    month, result["as_of_nyse_session"],
                    row["symbol"], row["rank"], row["display_position"],
                    row["ranking_score"], row["tie_count"],
                    result["report_status"], True,
                ])
        os.replace(tmp_json, out_json)
        os.replace(tmp_csv, out_csv)
    finally:
        tmp_json.unlink(missing_ok=True)
        tmp_csv.unlink(missing_ok=True)
    return {"json_report": str(out_json), "csv_rankings": str(out_csv)}
