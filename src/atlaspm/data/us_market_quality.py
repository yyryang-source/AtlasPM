"""Streaming exchange-calendared data-quality gate for current-US-listed stock quotes.

Works on the existing {date,symbol,adjusted_close} CSV emitted by
stock-sync-market, streaming by row with O(number of listed securities +
recent-window dates) memory. Audits quote freshness, history warmup, gaps,
exact listing membership and manifest timestamp. NO broker/order code.

The universe is the snapshot of CURRENT listings, not a point-in-time
historical universe; delisted historical stocks remain absent.
"""

from __future__ import annotations

from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
import re
from typing import Any

from atlaspm.us_market_universe import read_market_universe

SCHEMA = "atlaspm_us_market_data_audit_v1"
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")


def audit_current_us_stock_prices(
    *,
    universe_path: Path,
    prices_path: Path,
    as_of: str,
    min_history_sessions: int = 253,
    recent_sessions: int = 63,
    min_recent_coverage_pct: float = 85.0,
    min_month_end_coverage_pct: float = 90.0,
    min_eligible_coverage_pct: float = 75.0,
    min_eligible_names: int = 500,
    max_manifest_age_days: int = 7,
) -> dict[str, Any]:
    """Audit a whole-market input without requiring a multi-million-row dataframe.

    All parameters are research QC thresholds, not trade/entry thresholds.
    A YYYY-MM-DD as_of must be an actual NYSE session, and quotes from any
    later dates are excluded. A report marked ready is for research review,
    NOT for live portfolio or brokerage approval.
    """
    try:
        decision = date.fromisoformat(as_of)
        if decision.isoformat() != as_of:
            raise ValueError
    except ValueError as exc:
        raise ValueError("as-of must be YYYY-MM-DD") from exc
    for label, val in (
        ("min-history-sessions", min_history_sessions),
        ("recent-sessions", recent_sessions),
        ("min-eligible-names", min_eligible_names),
    ):
        if isinstance(val, bool) or not isinstance(val, int) or val < 1:
            raise ValueError(f"{label} must be a positive integer")
    if isinstance(max_manifest_age_days, bool) or not isinstance(max_manifest_age_days, int) or max_manifest_age_days < 0:
        raise ValueError("max-manifest-age-days must be a nonnegative integer")
    for label, pct in (
        ("min-recent-coverage-pct", min_recent_coverage_pct),
        ("min-month-end-coverage-pct", min_month_end_coverage_pct),
        ("min-eligible-coverage-pct", min_eligible_coverage_pct),
    ):
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not isfinite(pct) or not 0 <= pct <= 100:
            raise ValueError(f"{label} must be a finite percent in [0,100]")

    try:
        import pandas_market_calendars as mcal
    except ImportError as exc:
        raise ValueError("Install NYSE calendar: pip install -e '.[calendar]'") from exc
    nyse = mcal.get_calendar("NYSE")
    # Look far enough back to cover 63 + 253 NYSE sessions including holidays.
    days_to_scan = max(recent_sessions, min_history_sessions) * 3 + 60
    sessions = [
        stamp.date()
        for stamp in nyse.valid_days(
            start_date=(decision - timedelta(days=days_to_scan)).isoformat(),
            end_date=decision.isoformat(),
        )
    ]
    if not sessions or sessions[-1] != decision:
        raise ValueError(f"{as_of} is not an NYSE trading session")
    if len(sessions) < recent_sessions:
        raise ValueError("Insufficient NYSE calendar lookback for recent-sessions")
    trailing = set(sessions[-recent_sessions:])
    # This bound is fixed for an audit date. Avoid recomputing min(trailing)
    # for millions of historical price rows.
    trailing_start = sessions[-recent_sessions]

    manifest = read_market_universe(universe_path)
    catalog = {row["provider_symbol"].strip().upper(): row for row in manifest}
    unique_snapshots = {
        row.get("snapshot_utc", "").strip() for row in manifest if row.get("snapshot_utc", "").strip()
    }
    qc_reasons: list[str] = []
    snapshot_utc: str | None = None
    snapshot_age_days: int | None = None
    if len(unique_snapshots) != 1:
        qc_reasons.append("UNVERIFIED_LISTING_SNAPSHOT: no unique timestamp on listing records")
    else:
        snapshot_utc = next(iter(unique_snapshots))
        try:
            snapshot = datetime.fromisoformat(snapshot_utc.replace("Z", "+00:00"))
            if snapshot.tzinfo is None:
                raise ValueError("timestamp lacks timezone")
            snapshot_age_days = (decision - snapshot.astimezone(timezone.utc).date()).days
            if snapshot_age_days > max_manifest_age_days:
                qc_reasons.append(
                    f"STALE_LISTING_MANIFEST: {snapshot_age_days} days older than decision date"
                )
            if snapshot_age_days < -3:
                qc_reasons.append(
                    f"POSTDATED_LISTING_MANIFEST: {abs(snapshot_age_days)} days after decision date; "
                    "historical membership cannot be inferred"
                )
        except ValueError:
            qc_reasons.append("INVALID_LISTING_SNAPSHOT_TIME")

    total_rows = 0
    before_or_on = 0
    future_rows_ignored = 0
    rows_not_in_manifest = 0
    invalid_price_rows = 0
    off_calendar_recent_rows = 0
    example_unknown: set[str] = set()
    seen_rows: dict[str, int] = defaultdict(int)
    session_rows: dict[str, int] = defaultdict(int)
    recent_rows: dict[str, int] = defaultdict(int)
    last_onsession: dict[str, date] = {}
    recent_seen_dates: dict[str, set[date]] = defaultdict(set)
    last_seen: dict[str, date] = {}
    # Count ALL record duplicates / out-of-order by tracking just last
    # observed date per ticker. Supports both ticker-major and date-major
    # files as long as each ticker's own dates are chronologically ordered.
    duplicate_or_out_of_order = 0

    with prices_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {"date", "symbol", "adjusted_close"}.issubset(reader.fieldnames):
            raise ValueError("Quote CSV needs date,symbol,adjusted_close")
        for row in reader:
            total_rows += 1
            symbol = str(row.get("symbol") or "").strip().upper()
            if not _SYMBOL.fullmatch(symbol):
                invalid_price_rows += 1
                continue
            if symbol not in catalog:
                rows_not_in_manifest += 1
                if len(example_unknown) < 20:
                    example_unknown.add(symbol)
                continue
            try:
                raw_date = str(row["date"]).strip()
                day = date.fromisoformat(raw_date)
                if day.isoformat() != raw_date:
                    raise ValueError("non-ISO date")
                value = float(row["adjusted_close"])
                if not isfinite(value) or value <= 0:
                    raise ValueError("nonpositive/nonfinite adjusted close")
            except (ValueError, TypeError, OverflowError):
                invalid_price_rows += 1
                continue

            if symbol in last_seen and day <= last_seen[symbol]:
                duplicate_or_out_of_order += 1
                continue
            last_seen[symbol] = day
            if day > decision:
                future_rows_ignored += 1
                continue
            before_or_on += 1
            seen_rows[symbol] += 1
            if day in trailing:
                recent_seen_dates[symbol].add(day)
                recent_rows[symbol] += 1
                if day == decision:
                    last_onsession[symbol] = day
            elif day >= trailing_start:
                off_calendar_recent_rows += 1
            if day == decision:
                last_onsession[symbol] = day
                session_rows[symbol] += 1

    catalog_count = len(catalog)
    on_date = {s for s in catalog if s in last_onsession}
    eligible: list[str] = []
    only_short: list[str] = []
    recent_gap: list[str] = []
    stale: list[str] = []
    for symbol in sorted(catalog):
        if symbol not in on_date:
            stale.append(symbol)
        elif seen_rows[symbol] < min_history_sessions:
            only_short.append(symbol)
        elif 100 * len(recent_seen_dates[symbol]) / recent_sessions < min_recent_coverage_pct:
            recent_gap.append(symbol)
        else:
            eligible.append(symbol)
    on_date_count = len(on_date)
    eligible_count = len(eligible)
    month_end_coverage = round(100 * on_date_count / catalog_count, 2)
    eligible_coverage = round(100 * eligible_count / catalog_count, 2)
    if month_end_coverage < min_month_end_coverage_pct:
        qc_reasons.append(
            f"INSUFFICIENT_DECISION_CLOSE_COVERAGE: {month_end_coverage}% "
            f"< {min_month_end_coverage_pct}%"
        )
    if eligible_coverage < min_eligible_coverage_pct:
        qc_reasons.append(
            f"INSUFFICIENT_SCOREABLE_MARKET_COVERAGE: {eligible_coverage}% "
            f"< {min_eligible_coverage_pct}%"
        )
    if eligible_count < min_eligible_names:
        qc_reasons.append(
            f"INSUFFICIENT_WARMED_UP_STOCKS: {eligible_count} < {min_eligible_names}"
        )
    if invalid_price_rows:
        qc_reasons.append(f"INVALID_QUOTE_ROWS: {invalid_price_rows}")
    if rows_not_in_manifest:
        qc_reasons.append(
            f"QUOTES_OUTSIDE_LISTING_MANIFEST: {rows_not_in_manifest} rows"
        )
    if duplicate_or_out_of_order:
        qc_reasons.append(f"DUPLICATE_OR_NONMONOTONIC_QUOTE_ROWS: {duplicate_or_out_of_order}")
    if off_calendar_recent_rows:
        qc_reasons.append(
            f"OFF_NYSE_CALENDAR_RECENT_QUOTE_ROWS: {off_calendar_recent_rows}"
        )

    return {
        "schema": SCHEMA,
        "as_of_nyse_session": as_of,
        "data_scope": "current_US_exchange_listed_stock_candidates_only",
        "historical_point_in_time_membership_verified": False,
        "listed_stock_universe_count": catalog_count,
        "manifest_snapshot_utc": snapshot_utc,
        "manifest_age_at_decision_days": snapshot_age_days,
        "issue_classification_unverified_count": sum(
            row.get("classification") == "unverified_equity" for row in manifest
        ),
        "rows_scanned": total_rows,
        "valid_rows_as_of": before_or_on,
        "future_dated_quote_rows_ignored": future_rows_ignored,
        "invalid_quote_rows": invalid_price_rows,
        "duplicate_or_out_of_order_quote_rows": duplicate_or_out_of_order,
        "rows_outside_listing_manifest": rows_not_in_manifest,
        "out_of_manifest_symbols_sample": sorted(example_unknown),
        "off_nyse_calendar_recent_quote_rows": off_calendar_recent_rows,
        "unique_symbols_with_price_history": len(seen_rows),
        "stocks_with_exact_decision_close": on_date_count,
        "decision_close_coverage_pct": month_end_coverage,
        "recent_sessions_checked": recent_sessions,
        "minimum_recent_coverage_pct": float(min_recent_coverage_pct),
        "minimum_history_sessions": min_history_sessions,
        "eligible_history_and_freshness": eligible_count,
        "eligible_coverage_pct": eligible_coverage,
        "eligible_symbols": eligible,
        "missing_last_close_count": len(stale),
        "missing_last_close_examples": stale[:30],
        "insufficient_history_count": len(only_short),
        "insufficient_history_examples": only_short[:30],
        "recent_missing_sessions_count": len(recent_gap),
        "recent_missing_sessions_examples": recent_gap[:30],
        "minimum_required_stock_names": min_eligible_names,
        "minimum_month_end_coverage_pct": float(min_month_end_coverage_pct),
        "minimum_scoreable_coverage_pct": float(min_eligible_coverage_pct),
        "market_data_status": "REVIEW_REQUIRED" if qc_reasons else "READY_FOR_RESEARCH_REVIEW",
        "review_reasons": qc_reasons,
        "research_only": True,
        "automated_handoff_approved": False,
        "live_execution_enabled": False,
        "order_instructions": [],
        "warnings": [
            "Today's listed companies are NOT the point-in-time set of historic delisted or IPO names.",
            "This is an input data audit, not an independent validation of model predictive skill.",
            "Yahoo adjusted close may be retrospectively revised for dividends and corporate actions.",
            "One current manifest is not a substitute for issuer/instrument classification audit.",
            "This audit does not approve new positions or override AtlasPM risk controls.",
        ],
    }
