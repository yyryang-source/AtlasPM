"""Orchestrate the existing archive and factor builders; no second warehouse."""
from __future__ import annotations

from argparse import ArgumentParser
from hashlib import sha256
import json

from atlaspm.data import factor_db, hf_stock5m as hf, hf_stock5m_batch as qc, unified
from atlaspm.data.hf_stock5m_certification_v5 import fingerprint


def _month_list(start, end):
    plan = unified.archive_plan(start, end)
    if any(not row["within_advertised_archive"] for row in plan["months"]):
        raise unified.DataUnavailable("Outside advertised archive; no inferred coverage")
    return plan["months"]


def _report(**values):
    return {**values, "coverage_verified": False, "point_in_time_verified": False,
            "approved_for_model_training": False, "approved_for_backtest": False,
            "live_execution_enabled": False}


def maintain(start_month, end_month, *, execute=False, memory="256MB", threads=1):
    """Offline, single-writer maintenance. Reuse existing builders and QC.

    No download, raw deletion or automatic replacement of existing 5m files.
    Process oldest first and stop on missing input or failed QC. Existing
    factor and audit caches provide restart behavior. Never run concurrently
    with another writer to these same partitions.
    """
    if type(execute) is not bool:
        raise ValueError("execute must be boolean")
    inventory = _month_list(start_month, end_month)
    if not execute:
        return _report(mode="plan", months=inventory, network_downloads=0)
    results = []
    for item in inventory:
        month = item["month"]
        record = {"month": month}
        results.append(record)
        try:
            raw, bars = hf.raw_path(month), hf.output_path(month)
            if not bars.is_file():
                if not raw.is_file():
                    raise unified.DataUnavailable("Missing raw and converted partition")
                before = unified._version(raw)
                hf.convert(raw, bars, "rth")
                if unified._version(raw) != before:
                    raise unified.DataQualityError("Raw changed during conversion; review output")
                record["bars"] = "converted"
            else:
                record["bars"] = "existing_not_replaced"
            signature = unified._version(bars)
            audit = qc.run(month, month, session="rth", resume=True, dry_run=False)
            record["audit"] = audit["status_counts"]
            if not audit["all_months_structurally_audited"]:
                raise unified.DataQualityError("Existing 5m audit did not pass structural checks")
            if unified._version(bars) != signature:
                raise unified.DataQualityError("Bars changed during audit")
            built = factor_db.build(month, source="hf", memory=memory, threads=threads)
            record["factors"] = built["state"]
            if unified._version(bars) != signature:
                raise unified.DataQualityError("Bars changed during factor build; review derivatives")
            record["status"] = "EXPLORATORY_REVIEW_REQUIRED"
        except Exception as exc:
            record.update(status="BLOCKED", error=f"{type(exc).__name__}: {exc}")
            break
    return _report(mode="maintenance", months=results, network_downloads=0,
                   remaining_months=len(inventory)-len(results),
                   all_requested_months_processed=len(results)==len(inventory)
                   and all(r["status"]!="BLOCKED" for r in results))


def _content(path):
    before = unified._version(path)
    item = fingerprint(path)
    if unified._version(path) != before:
        raise unified.DataQualityError("File changed during fingerprint")
    return {"sha256": item["sha256"], "bytes": item["bytes"]}


def factor_preflight(start_month, end_month, *, memory="256MB", threads=1):
    """Inspect actual stored HF factors and content versions, without training.

    A deterministic report is evidence for a reviewer, not a capability token.
    Hash current bars, factor file and existing lineage metadata. Compare the
    factor builder's recorded daily-history fingerprint to current daily files.
    The builder uses stat fingerprints, not cryptographic lineage proofs.
    """
    inventory = _month_list(start_month, end_month)
    records = []
    hash_cache = {}

    def content(path):
        signature = unified._version(path)
        key = (path, signature)
        if key not in hash_cache:
            hash_cache[key] = _content(path)
        if unified._version(path) != signature:
            raise unified.DataQualityError("File changed during preflight")
        return hash_cache[key]

    for item in inventory:
        month = item["month"]
        record = {"month": month, "blockers": []}
        records.append(record)
        bars = hf.output_path(month)
        daily = factor_db._path(month, "hf", "rth", "daily")
        factors = factor_db._path(month, "hf", "rth", "factors")
        meta = factors.with_suffix(".json")
        daily_meta = daily.with_suffix(".json")
        paths = (bars, daily, daily_meta, factors, meta)
        if not all(p.is_file() for p in paths):
            record["blockers"].append("MISSING_BAR_FACTOR_OR_LINEAGE_FILE")
            continue
        try:
            signatures = {p: unified._version(p) for p in paths}
            record["content"] = {name: content(p) for name, p in
                                 zip(("bars", "daily", "daily_metadata", "factors", "factor_metadata"), paths)}
            m = factor_db._read_meta(meta)
            d = factor_db._read_meta(daily_meta)
            if not m or not d or m.get("version") != factor_db.VERSION or d.get("version") != factor_db.VERSION:
                record["blockers"].append("MISSING_OR_UNSUPPORTED_FACTOR_METADATA")
            else:
                if (m.get("month"), m.get("source"), m.get("session")) != (month, "hf", "rth"):
                    record["blockers"].append("FACTOR_METADATA_SCOPE_MISMATCH")
                if (d.get("month"), d.get("source"), d.get("session")) != (month, "hf", "rth"):
                    record["blockers"].append("DAILY_METADATA_SCOPE_MISMATCH")
                if d.get("input_fingerprint") != factor_db._fingerprint([bars]):
                    record["blockers"].append("STALE_DAILY_INPUT")
                first = factor_db._month_first(month)
                directory = daily.parent.parent.parent
                history = sorted(p for p in directory.glob("year=*/month=*/*.parquet")
                                 if 0 <= factor_db._month_diff(first, factor_db._month_first(p.stem[-7:])) <= factor_db.LOOKBACK_MONTHS)
                if not history or m.get("history_fingerprint") != factor_db._fingerprint(history):
                    record["blockers"].append("STALE_FACTOR_HISTORY")
                # A stale historical daily projection also invalidates derived
                # factors even if the derived daily file itself has not changed.
                record["lookback_content"] = {}
                for historic in history:
                    hmonth = historic.stem[-7:]
                    source = hf.output_path(hmonth)
                    hmeta_path = historic.with_suffix(".json")
                    hmeta = factor_db._read_meta(hmeta_path)
                    if (not source.is_file() or not hmeta or
                            hmeta.get("input_fingerprint") != factor_db._fingerprint([source])):
                        record["blockers"].append("STALE_OR_MISSING_LOOKBACK:" + hmonth)
                        continue
                    record["lookback_content"][hmonth] = {
                        "bars": content(source), "daily": content(historic),
                        "metadata": content(hmeta_path),
                    }
            report = factor_db.audit(month, source="hf", memory=memory, threads=threads)
            record["factor_rows"] = report["rows"]
            if not report["valid"] or (m and m.get("rows") != report["rows"]):
                record["blockers"].append("FACTOR_STRUCTURE_OR_ROW_COUNT_MISMATCH")
            if any(unified._version(p) != sig for p, sig in signatures.items()):
                record["blockers"].append("INPUT_CHANGED_DURING_PREFLIGHT")
        except Exception as exc:
            record["blockers"].append("PREFLIGHT_ERROR:" + type(exc).__name__)
    # Catch changes to previously hashed lookback files across month boundaries.
    if any(not p.is_file() or unified._version(p) != sig for p, sig in hash_cache):
        for record in records:
            record["blockers"].append("LOOKBACK_CHANGED_DURING_PREFLIGHT")
    stable = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return _report(mode="factor_preflight", months=records, evidence_id=sha256(stable).hexdigest(),
                   structural_preflight_passed=all(not r["blockers"] for r in records),
                   blockers=["INDEPENDENT_SOURCE_AUTHENTICITY_AND_RIGHTS_REQUIRED",
                             "HISTORICAL_UNIVERSE_AND_DELISTING_COMPLETENESS_REQUIRED",
                             "CORPORATE_ACTION_COMPLETENESS_REQUIRED",
                             "FEATURE_PUBLICATION_TIME_AND_ADJUSTMENT_VERIFICATION_REQUIRED",
                             "INDEPENDENT_PRICE_RECONCILIATION_REQUIRED"])


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "maintain", "factor-preflight"))
    parser.add_argument("--start-month", required=True)
    parser.add_argument("--end-month", required=True)
    parser.add_argument("--execute", action="store_true", help="Explicit local derived-data maintenance")
    parser.add_argument("--memory", default="256MB")
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args()
    if args.execute and args.action != "maintain":
        parser.error("--execute only applies to maintain")
    options = dict(memory=args.memory, threads=args.threads)
    if args.action == "factor-preflight":
        report = factor_preflight(args.start_month, args.end_month, **options)
    else:
        report = maintain(args.start_month, args.end_month, execute=args.execute, **options)
    print(json.dumps(report, indent=2))
    if report.get("all_requested_months_processed") is False or report.get("structural_preflight_passed") is False:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
