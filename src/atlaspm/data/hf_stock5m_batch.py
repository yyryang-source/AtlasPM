"""Resumable offline full-history QC for local HF monthly five-minute Parquet.

This is a descriptive read-only audit of source data. No network, training,
broker, order, implicit certification, or changes to the price lake.
Only small JSON/CSV audit metadata is written under gitignored local_data/.
"""
from __future__ import annotations

import csv
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

from atlaspm.data import hf_stock5m, hf_stock5m_audit
from atlaspm.data.market_db import private_path

SCHEMA = "atlaspm_hf_full_history_audit_v2"
FIELDS = (
    "month", "status", "five_minute_bars", "unique_symbols",
    "nyse_expected_sessions", "observed_trade_dates", "missing_nyse_sessions",
    "invalid_five_minute_bars", "duplicate_symbol_timestamp_rows",
    "out_of_nyse_session_bars", "sparse_five_minute_buckets",
    "total_5m_buckets_quarantined", "invalid_raw_source_rows", "review_reasons",
    "error",
)
REVIEW = "REVIEW_REQUIRED"
STRUCTURAL = "STRUCTURAL_QC_FAILED"
MISSING = "MISSING_FILE"
ERROR = "AUDIT_ERROR"


def _file_signature(path: Path) -> dict | None:
    """Cheap resume invalidation; NOT a cryptographic verification of the source."""
    if not path.is_file():
        return None
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _audit_path(month: str, session: str) -> Path:
    return private_path(hf_stock5m.ROOT / "quality" / "audits" /
                        f"session={session}" / f"{month}.json")


def _meta_path(month: str, session: str) -> Path:
    return private_path(hf_stock5m.ROOT / "quality" / "batch" /
                        f"session={session}" / "months" / f"{month}.json")


def _summary_dir(session: str) -> Path:
    return private_path(hf_stock5m.ROOT / "quality" / "batch" / f"session={session}")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                row = dict(record)
                reasons = row.get("review_reasons", [])
                row["review_reasons"] = ";".join(reasons) if isinstance(reasons, list) else str(reasons)
                writer.writerow(row)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    # Audit JSON is tiny. Do not hash 25+ GiB of Parquet just for resume checks.
    return sha256(path.read_bytes()).hexdigest()


def _record(month: str, report: dict) -> dict:
    problems = report.get("structural_issues", [])
    status = STRUCTURAL if problems or report.get("quality_status") == STRUCTURAL else REVIEW
    return {
        "month": month, "status": status,
        "five_minute_bars": int(report["five_minute_bars"]),
        "unique_symbols": int(report["unique_symbols"]),
        "nyse_expected_sessions": int(report["nyse_expected_sessions"]),
        "observed_trade_dates": int(report["observed_trade_dates"]),
        "missing_nyse_sessions": len(report["missing_whole_trading_dates"]),
        "invalid_five_minute_bars": int(report["invalid_five_minute_bars"]),
        "duplicate_symbol_timestamp_rows": int(report["duplicate_symbol_timestamp_rows"]),
        "out_of_nyse_session_bars": report.get("out_of_nyse_session_bars"),
        "sparse_five_minute_buckets": int(report["sparse_five_minute_buckets"]),
        "total_5m_buckets_quarantined": int(
            report.get("total_5m_buckets_quarantined")
            if report.get("total_5m_buckets_quarantined") is not None
            else (report.get("ambiguous_buckets_quarantined") or 0)),
        "invalid_raw_source_rows": int(report.get("invalid_raw_source_rows") or 0),
        "review_reasons": list(report.get("review_reasons") or []),
        "structural_issues": list(problems),
        "error": "",
    }


def _cached(month: str, session: str, output: Path, qc: Path) -> dict | None:
    checkpoint = _meta_path(month, session)
    report_path = _audit_path(month, session)
    if not checkpoint.is_file() or not report_path.is_file():
        return None
    try:
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        if (data["schema"] != SCHEMA or data["monthly_audit_schema"] != hf_stock5m_audit.SCHEMA
                or data["month"] != month or data["session"] != session
                or data["parquet_signature"] != _file_signature(output)
                or data["qc_signature"] != _file_signature(qc)
                or data["audit_sha256"] != _sha256(report_path)):
            return None
        record = data["record"]
        if (record["month"] != month or record["status"] not in (REVIEW, STRUCTURAL)
                or not isinstance(record.get("five_minute_bars"), int)):
            return None
        return record
    except (OSError, ValueError, TypeError, KeyError):
        return None


def run(start_month: str = "2000-01", end_month: str = "2026-03", *,
        session: str = "rth", max_months: int = 0,
        resume: bool = True, dry_run: bool = False) -> dict:
    """Audit monthly output; checkpoints revalidate file stats and audit JSON hash.

    Reports and checkpoints are local metadata only. Source-file hashes are NOT
    checked; a source can conceivably change with the same size and mtime.
    --no-resume forces a new scan. A quality status never approves training.
    """
    if session not in ("rth", "all"):
        raise ValueError("session must be rth or all")
    chosen = hf_stock5m.months(start_month, end_month, max_months)
    if not chosen:
        raise ValueError("No available months in the selected archive period")
    requested = {
        "schema": SCHEMA, "provider": hf_stock5m.REPO_ID,
        "period_requested": [start_month, end_month],
        "months_selected": len(chosen), "selected_first": chosen[0],
        "selected_last": chosen[-1], "session": session,
        "local_only": True, "network_downloads": 0,
        "coverage_verified": False, "data_rights_verified": False,
        "historical_point_in_time_membership_verified": False,
        "corporate_actions_verified": False,
        "approved_for_model_training": False,
        "live_execution_enabled": False, "research_only": True,
    }
    if dry_run:
        present = sum(hf_stock5m.output_path(month, session).is_file() for month in chosen)
        return {**requested, "mode": "dry_run",
                "parquet_files_present": present,
                "parquet_files_missing": len(chosen) - present}
    records = []
    cached_count = 0
    for index, month in enumerate(chosen, 1):
        output = hf_stock5m.output_path(month, session)
        _, qc = hf_stock5m.quality_paths(output)
        if not output.is_file() or output.stat().st_size < 100:
            records.append({"month": month, "status": MISSING,
                            "error": "Missing/empty local 5m Parquet"})
            print(f"[{index}/{len(chosen)}] {month}: {MISSING}", file=sys.stderr, flush=True)
            continue
        previous = _cached(month, session, output, qc) if resume else None
        if previous is not None:
            cached_count += 1
            records.append(previous)
            print(f"[{index}/{len(chosen)}] {month}: cached {previous['status']}",
                  file=sys.stderr, flush=True)
            continue
        try:
            report = hf_stock5m_audit.audit(month, session=session, save=True)
            monthly_report = _audit_path(month, session)
            if not monthly_report.is_file():
                raise ValueError("Monthly audit did not write its report")
            record = _record(month, report)
            checkpoint = {
                "schema": SCHEMA, "monthly_audit_schema": hf_stock5m_audit.SCHEMA,
                "month": month, "session": session,
                "parquet_signature": _file_signature(output),
                "qc_signature": _file_signature(qc),
                "audit_sha256": _sha256(monthly_report),
                "record": record,
            }
            _write_json(_meta_path(month, session), checkpoint)
        except Exception as exc:
            # Audit the remaining months even if a local partition is corrupt.
            # Never cache a failed run as successful; each retry must rescan it.
            record = {"month": month, "status": ERROR,
                      "error": f"{type(exc).__name__}: {exc}"[:500]}
        records.append(record)
        print(f"[{index}/{len(chosen)}] {month}: {record['status']}",
              file=sys.stderr, flush=True)

    counts = {status: sum(r["status"] == status for r in records)
              for status in (REVIEW, STRUCTURAL, MISSING, ERROR)}
    audited = counts[REVIEW] + counts[STRUCTURAL]
    summary = {
        **requested, "mode": "audit", "completed_months": audited,
        "resumed_months": cached_count, "status_counts": counts,
        "months_missing": [r["month"] for r in records if r["status"] == MISSING],
        "months_audit_error": [r["month"] for r in records if r["status"] == ERROR],
        "months_structural_failed": [r["month"] for r in records if r["status"] == STRUCTURAL],
        "total_five_minute_bars": sum(r.get("five_minute_bars", 0) for r in records),
        "total_quarantined_5m_buckets": sum(
            r.get("total_5m_buckets_quarantined", 0) for r in records),
        "total_invalid_raw_source_rows": sum(r.get("invalid_raw_source_rows", 0)
                                              for r in records),
        "total_missing_nyse_sessions": sum(r.get("missing_nyse_sessions", 0)
                                            for r in records),
        "all_months_structurally_audited": audited == len(chosen) and counts[STRUCTURAL] == 0,
        "warning": "Structural QC is not a data license, PIT universe or model-training approval.",
    }
    dest = _summary_dir(session)
    summary_file = dest / "latest_summary.json"
    csv_file = dest / "latest_months.csv"
    _write_csv(csv_file, records)
    summary["summary_path"] = str(summary_file)
    summary["months_csv_path"] = str(csv_file)
    _write_json(summary_file, summary)
    return summary
