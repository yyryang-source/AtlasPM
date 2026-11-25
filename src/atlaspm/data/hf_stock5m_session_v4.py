"""Offline V4 session diagnostics and conservative NYSE research projection.

Canonical data remains untouched. A calendar projection is not venue identity,
corporate-action verification, a license, or permission to train or trade.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, time
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from atlaspm.data import hf_stock5m as hf
from atlaspm.data import hf_stock5m_audit as qc
from atlaspm.data.market_db import private_path

SCHEMA = "atlaspm_hf_session_quality_v4"
WIDTH = hf.FIVE_MINUTES_MS
ET = ZoneInfo("America/New_York")
OUTSIDE = ("NON_NYSE_DATE", "BEFORE_OPEN", "AFTER_EARLY_CLOSE", "AFTER_REGULAR_CLOSE")
GUARDS = {
    "research_only": True, "local_only": True, "network_downloads": 0,
    "approved_for_model_training": False, "live_execution_enabled": False,
    "coverage_verified": False, "corporate_actions_verified": False,
    "historical_point_in_time_membership_verified": False,
    "symbol_primary_listing_exchange_verified": False, "data_rights_verified": False,
}
CLASSIFY = f"""
CASE
    WHEN a.timestamp_ms IS NULL OR a.trade_date_et IS NULL
         OR a.timestamp_ms % {WIDTH} <> 0 THEN 'INVALID_SESSION_KEY'
    WHEN s.trade_date IS NULL THEN 'NON_NYSE_DATE'
    WHEN a.timestamp_ms + {WIDTH} <= s.open_ms THEN 'BEFORE_OPEN'
    WHEN a.timestamp_ms >= s.close_ms AND s.early_close THEN 'AFTER_EARLY_CLOSE'
    WHEN a.timestamp_ms >= s.close_ms THEN 'AFTER_REGULAR_CLOSE'
    WHEN a.timestamp_ms < s.open_ms OR a.timestamp_ms + {WIDTH} > s.close_ms
         THEN 'PARTIAL_BOUNDARY'
    ELSE 'FULL_NYSE_SLOT'
END
"""


def calendar_rows(month: str):
    # Same historical calendar as V3; do not guess special hours.
    return [(day, opening, closing,
             datetime.fromtimestamp(closing / 1000, timezone.utc).astimezone(ET).time()
             < time(16))
            for day, opening, closing in qc._calendar(month)]


def install_calendar(cx, rows):
    cx.execute("""CREATE OR REPLACE TEMP TABLE v4_nyse(
        trade_date DATE, open_ms BIGINT, close_ms BIGINT, early_close BOOLEAN)""")
    cx.executemany("INSERT INTO v4_nyse VALUES (?,?,?,?)", rows)


def _source(month):
    path = hf.output_path(month, "rth")
    if not path.is_file():
        raise ValueError(f"Missing local RTH partition: {path}")
    return path


def _signature(path: Path):
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _join(path):
    return (f"read_parquet('{hf._quote(path)}') a LEFT JOIN v4_nyse s "
            "ON a.trade_date_et=s.trade_date")


def install_research_view(cx, month: str):
    """Create a connection-local full-NYSE-slot candidate view, never a training view.

    Partial buckets are excluded, not clipped: minute composition is unknown.
    No data is copied or deleted. Existing OHLCV/identity/rights QC still applies.
    """
    path = _source(month)
    install_calendar(cx, calendar_rows(month))
    cx.execute(f"""CREATE OR REPLACE TEMP VIEW v4_research_bars AS
        SELECT a.*, a.timestamp_ms + {WIDTH} AS bar_available_at_ms
        FROM {_join(path)} WHERE ({CLASSIFY}) = 'FULL_NYSE_SLOT'""")
    return "v4_research_bars"


def diagnose(month: str, prior: dict | None = None):
    path = _source(month)
    signature = _signature(path)
    rows = calendar_rows(month)
    digest = hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()
    cx = hf._open_duckdb()
    try:
        install_calendar(cx, rows)
        # Only date/timestamp columns are scanned. Aggregate to day/category,
        # not individual bars, and never materialize the whole archive in Python.
        grouped = cx.execute(f"""
            SELECT a.trade_date_et, ({CLASSIFY}) AS category, count(*) AS n,
                   min(a.timestamp_ms), max(a.timestamp_ms)
            FROM {_join(path)}
            GROUP BY 1,2 ORDER BY 1,2
        """).fetchall()
    finally:
        cx.close()
    if _signature(path) != signature:
        raise ValueError("Source changed during diagnostic scan")
    counts = Counter()
    details = []
    for day, category, n, first, last in grouped:
        counts[category] += int(n)
        if category != "FULL_NYSE_SLOT":
            details.append({"date": day.isoformat() if day else None,
                            "category": category, "bars": int(n),
                            "first_bucket_ms": first, "last_bucket_ms": last})
    total = sum(counts.values())
    outside = sum(counts[x] for x in OUTSIDE)
    prior_issues = list(prior.get("structural_issues", [])) if prior else []
    other_issues = [i for i in prior_issues if not i.startswith("OUTSIDE_NYSE_SESSIONS:")]
    matches_prior = bool(prior and prior.get("schema") == qc.SCHEMA
                         and prior.get("five_minute_bars") == total
                         and prior.get("out_of_nyse_session_bars") == outside)
    if not total or counts["INVALID_SESSION_KEY"] or other_issues:
        status = "STRUCTURAL_REVIEW_REQUIRED"
    elif not matches_prior:
        status = "PRIOR_AUDIT_RECONCILIATION_REQUIRED"
    else:
        status = "SESSION_DIAGNOSTIC_COMPLETE"
    return {"month": month, "status": status, "source": str(path),
            "source_signature": signature, "calendar_sha256": digest,
            "total_bars": total, "outside_nyse_bars": outside,
            "research_candidate_bars": counts["FULL_NYSE_SLOT"],
            "excluded_from_candidate_bars": total - counts["FULL_NYSE_SLOT"],
            "counts": dict(sorted(counts.items())), "exceptions_by_date": details,
            "prior_counts_match": matches_prior, "prior_structural_issues": prior_issues,
            "other_structural_issues": other_issues, **GUARDS}


def calendar_digest(month):
    return hashlib.sha256(json.dumps(calendar_rows(month), default=str).encode()).hexdigest()


def install_early_close_projection(cx, month):
    """Only a fully reconciled, pure early-close disagreement is eligible.

    Fresh V3 audit and V4 diagnostic; do not trust a caller's report or
    change canonical QC. The source is an unverified cross-venue feed.
    """
    path = _source(month)
    before = _signature(path)
    prior = qc.audit(month, session="rth", save=False)
    report = diagnose(month, prior)
    counts = report["counts"]
    excluded = counts.get("AFTER_EARLY_CLOSE", 0)
    retained = counts.get("FULL_NYSE_SLOT", 0)
    if (prior["quality_status"] != "STRUCTURAL_QC_FAILED"
            or prior["structural_issues"] != [f"OUTSIDE_NYSE_SESSIONS:{excluded}"]
            or prior["missing_whole_trading_dates"]
            or report["status"] != "SESSION_DIAGNOSTIC_COMPLETE"
            or report["prior_counts_match"] is not True
            or set(counts) != {"AFTER_EARLY_CLOSE", "FULL_NYSE_SLOT"}
            or excluded <= 0 or retained <= 0
            or retained + excluded != report["total_bars"]
            or report["source_signature"] != before):
        raise ValueError("EARLY_CLOSE_PROJECTION_NOT_ELIGIBLE")
    view = install_research_view(cx, month)
    if (cx.execute(f"SELECT count(*) FROM {view}").fetchone()[0] != retained
            or calendar_digest(month) != report["calendar_sha256"]
            or _signature(path) != before):
        raise ValueError("EARLY_CLOSE_PROJECTION_RECONCILIATION_FAILED")
    return view, {"policy": "nyse_full_slots_early_close_v1",
                  "calendar_sha256": report["calendar_sha256"],
                  "canonical_quality_status": prior["quality_status"],
                  "canonical_bars": report["total_bars"],
                  "retained_bars": retained, "excluded_bars": excluded}


def _prior(month):
    path = private_path(hf.ROOT / "quality" / "audits" / "session=rth" / f"{month}.json")
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        report = json.load(f)
    if report.get("month") != month or report.get("session") != "rth":
        raise ValueError(f"Invalid prior report identity: {month}")
    return report


def run(start="2000-01", end="2026-03", *, all_months=False, dry_run=False):
    """Default: scan failed/partial or missing/old audit months, not all 2.27B bars."""
    selected = hf.months(start, end, 0)
    if not selected:
        raise ValueError("No available archive months selected")
    results, targets, skipped = [], [], []
    counts = Counter()
    for month in selected:
        try:
            prior = _prior(month)
            needs_scan = (all_months or not prior or prior.get("schema") != qc.SCHEMA
                          or prior.get("quality_status") != "REVIEW_REQUIRED"
                          or bool(prior.get("non_5m_aligned_nyse_sessions")))
            if not needs_scan:
                skipped.append(month)
                continue
            targets.append(month)
            if not dry_run:
                item = diagnose(month, prior)
                results.append(item)
                counts.update(item["counts"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            results.append({"month": month, "status": "DIAGNOSTIC_ERROR",
                            "error": str(exc)})
        except Exception as exc:
            # DuckDB and calendar errors are recorded per partition, never passed.
            results.append({"month": month, "status": "DIAGNOSTIC_ERROR",
                            "error": f"{type(exc).__name__}: {exc}"})
    summary = {"schema": SCHEMA, "period": [start, end], "dry_run": dry_run,
               "months_selected": len(selected), "target_months": targets,
               "skipped_prior_review_months": skipped,
               "months_scanned": sum("counts" in x for x in results),
               "status_counts": dict(Counter(x["status"] for x in results)),
               "category_counts": dict(sorted(counts.items())),
               "outside_nyse_bars": sum(counts[x] for x in OUTSIDE),
               "months": results, **GUARDS,
               "warning": "NYSE projection only; excluded bars are not proven corrupt. Prior QC status is unchanged."}
    if not dry_run:
        dest = private_path(hf.ROOT / "quality" / "session_v4" / "latest.json")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".json.part")
        summary["summary_path"] = str(dest)
        try:
            tmp.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
    return summary
