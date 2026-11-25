"""Offline triage of SEC identity leads against local observed market symbols.

This is a retrospective exact-symbol overlap diagnostic, not source
corroboration, share-class mapping, historical membership or admission.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json

from atlaspm.data import hf_stock5m as hf
from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data import hf_stock5m_identity_v56 as identity
from atlaspm.data import hf_stock5m_session_v4 as v4
from atlaspm.data.market_db import private_path

BASE = identity.BASE / "market_review"
MAX_SYMBOLS = 500


def _candidates(page):
    """Consume freshly re-extracted local observations, never edited reports."""
    rows = []
    for filing in page["results"]:
        for field, key, kind in (
            ("observations", "symbol", "COVER_OBSERVATION"),
            ("legacy_listing_text_leads", "symbol_candidate", "LISTING_TEXT"),
            ("legacy_symbol_change_text_leads", "old_symbol_candidate", "CHANGE_OLD"),
            ("legacy_symbol_change_text_leads", "new_symbol_candidate", "CHANGE_NEW"),
        ):
            for lead in filing.get(field, []):
                symbol = lead[key]
                if not identity.SYMBOL.fullmatch(symbol):
                    raise ValueError("UNSUPPORTED_REVIEW_SYMBOL")
                rows.append({
                    "cik": filing["cik"], "issuer": filing["issuer"],
                    "symbol": symbol, "kind": kind,
                    "source_url": filing["source_url"],
                    "source_sha256": filing["sha256"],
                    "filed_date": filing["filed_date"],
                    "known_date_lower_bound": filing["filed_date"],
                    "reported_change_date": lead.get("reported_change_date"),
                    "reported_exchange": lead.get("exchange_reported",
                        lead.get("old_exchange_reported" if kind == "CHANGE_OLD"
                                 else "new_exchange_reported")),
                    "source_lead": lead,
                    "security_id": None, "valid_from": None, "valid_to": None,
                })
    return rows


def _scan_month(month, symbols):
    path = hf.output_path(month, "rth")
    if not path.is_file():
        return {"month": month, "status": "MISSING_LOCAL_PARTITION"}, []
    before = v4._signature(path)
    calendar = v4.calendar_digest(month)
    cx = hf._open_duckdb()
    try:
        cx.execute("SET memory_limit='512MB'")
        cx.execute("SET threads=2")
        view = v4.install_research_view(cx, month)
        rows = cx.execute(f"""
            SELECT symbol, CAST(trade_date_et AS VARCHAR), count(*)
            FROM {view} WHERE symbol IN (SELECT unnest(?))
            GROUP BY 1,2 ORDER BY 1,2
        """, [symbols]).fetchall()
    finally:
        cx.close()
    if before != v4._signature(path) or calendar != v4.calendar_digest(month):
        raise ValueError("MARKET_REVIEW_SOURCE_CHANGED")
    return {"month": month, "status": "SCANNED",
            "source_signature": before, "calendar_sha256": calendar,
            "matched_symbol_days": len(rows)}, rows


def _day_summary(days, cutoff):
    return {"before": sum(d < cutoff for d in days),
            "on": sum(d == cutoff for d in days),
            "after": sum(d > cutoff for d in days)}


def run(year, start_month, end_month, *, max_issuers=20, after_cik=None,
        reference_as_of=None, reference_file=None):
    if reference_file is not None and reference_as_of is None:
        raise ValueError("Reference file requires an explicit --reference-as-of cutoff")
    months = hf.months(start_month, end_month, 0)
    if (not months or months[0] != start_month or months[-1] != end_month
            or len(months) > 12 or any(m[:4] != str(year) for m in months)):
        raise ValueError("Review requires 1..12 available archive months within filing year")
    # Reuse the existing collector's hash/header checks and bounded cache-only
    # path. Never accept a user's edited candidate JSON as source evidence.
    page = identity.run(year, max_issuers=max_issuers, after_cik=after_cik,
                        periodic_first=True, fetch=False)
    candidates = _candidates(page)
    symbols = sorted({r["symbol"] for r in candidates})
    if len(symbols) > MAX_SYMBOLS:
        raise ValueError("MARKET_REVIEW_SYMBOL_BUDGET: use a smaller issuer page")
    if len(candidates) > 4000:
        raise ValueError("MARKET_REVIEW_CANDIDATE_BUDGET: use a smaller issuer page")
    days = defaultdict(dict)
    scanned = []
    for month in months:
        if not symbols:
            scanned.append({"month": month, "status": "NO_CANDIDATE_SYMBOLS"})
            continue
        report, rows = _scan_month(month, symbols)
        scanned.append(report)
        for symbol, day, count in rows:
            days[symbol][day] = int(count)
    complete = bool(symbols) and all(x["status"] == "SCANNED" for x in scanned)
    by_symbol = defaultdict(set)
    for row in candidates:
        by_symbol[row["symbol"]].add(row["cik"])
    for row in candidates:
        observed = days[row["symbol"]]
        dates = sorted(observed)
        row.update(
            observed_symbol_days=len(dates),
            observed_full_slot_bars=sum(observed.values()),
            first_observed_date=dates[0] if dates else None,
            last_observed_date=dates[-1] if dates else None,
            relative_to_filing_date=_day_summary(dates, row["filed_date"]),
            symbol_claimed_by_multiple_sampled_issuers=len(by_symbol[row["symbol"]]) > 1,
            market_overlap_status="OBSERVED_EXACT_SYMBOL" if dates else
                ("NOT_OBSERVED_IN_SCANNED_MONTHS" if complete else "INSUFFICIENT_SCAN"),
        )
        if row["reported_change_date"]:
            row["relative_to_reported_change_date"] = _day_summary(
                dates, row["reported_change_date"])
    result = {
        "schema": "atlaspm_historical_identity_market_review_v1",
        **v4.GUARDS,
        "model_registry_admission": False,
        "broker_order_submission_enabled": False,
        "historical_security_identity_verified": False,
        "independent_source_verified": False,
        "approved_for_backtest": False,
        "certified_identities": 0, "claims_ingested": 0,
        "status": "MARKET_OVERLAP_REVIEW_REQUIRED",
        "year": year, "period": [start_month, end_month],
        "source_page": page["report_path"],
        "index_partition_sha256": page["index_partition_sha256"],
        "page_complete": page["page_complete"],
        "page_advance_allowed": page["page_advance_allowed"],
        "resume_after_cik": page["resume_after_cik"],
        "selected_issuers": page["selected_issuers"],
        "filings_with_bodies": page["filings_with_bodies"],
        "scan_complete": complete,
        "unique_candidate_symbols": len(symbols),
        "observed_candidate_symbols": sum(bool(days[s]) for s in symbols),
        "candidate_rows": len(candidates),
        "months": scanned, "candidates": candidates,
        "warning": ("Exact symbol overlap is not independent identity evidence. "
                    "Pre-filing bars are retrospective only; date-only filing-day "
                    "information is not prior-known. Full calendar slots do not "
                    "establish OHLCV quality, listing, or security continuity."),
    }
    if reference_as_of is not None:
        from atlaspm.data import hf_stock5m_identity_reference_review as reference
        result["reference_review"] = reference.review(
            candidates, days, as_of=reference_as_of, reference_file=reference_file,
            scan_complete=complete)
    path = BASE / "latest.json"
    result["report_path"] = str(private_path(path))
    ev._json(path, result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--start-month", required=True)
    p.add_argument("--end-month", required=True)
    p.add_argument("--max-issuers", type=int, default=20)
    p.add_argument("--after-cik")
    p.add_argument("--reference-as-of", help="Exclusive evidence cutoff; also builds review queue without reference file")
    p.add_argument("--reference-file", help="Reviewed external interval JSONL under local_data")
    a = p.parse_args()
    print(json.dumps(run(a.year, a.start_month, a.end_month,
                         max_issuers=a.max_issuers, after_cik=a.after_cik,
                         reference_as_of=a.reference_as_of, reference_file=a.reference_file),
                     indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
