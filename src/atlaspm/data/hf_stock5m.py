"""Research-only Hugging Face 1-minute US equities -> local 5-minute Parquet.

The dataset is publicly downloadable, but upstream Finnhub-derived-data rights
are NOT established by public hosting. Never redistribute its price data.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import os
import sys

from atlaspm.data.market_db import private_path

REPO_ID = "mito0o852/OHLCV-1m"
EARLIEST_MONTH = "1992-01"
LATEST_ADVERTISED_MONTH = "2026-03"  # snapshot; NOT a promise of present-day bars
ROOT = Path("local_data/stock_5m/huggingface")
FIVE_MINUTES_MS = 300_000
MAX_AMBIGUOUS_FRACTION = 0.001  # 0.1% of month/session ticker-buckets
MAX_AMBIGUOUS_BUCKETS = 10_000  # absolute fail-closed guard
AMBIGUOUS = '(source_minute_bars > 5 OR source_minute_bars <> unique_source_minutes)'
REQUIRED = {"timestamp", "ticker", "open", "high", "low", "close", "volume"}
MAX_INVALID_SOURCE_ROWS = 100
MAX_INVALID_SOURCE_FRACTION = 0.0001  # 0.01%: tiny, explicitly quarantined anomalies only
SOURCE_VALID = """
ticker IS NOT NULL AND length(trim(ticker)) BETWEEN 1 AND 32
AND timestamp IS NOT NULL
AND isfinite(open) AND isfinite(high) AND isfinite(low)
AND isfinite(close) AND isfinite(volume)
AND open > 0 AND high > 0 AND low > 0 AND close > 0
AND volume >= 0
AND low <= least(open,close) AND high >= greatest(open,close)
AND high >= low
"""
IDENTIFIABLE = ("ticker IS NOT NULL AND length(trim(ticker)) BETWEEN 1 AND 32 "
                "AND timestamp IS NOT NULL")


def validate_month(value: str) -> str:
    if len(value) != 7 or value[4] != "-":
        raise ValueError("Month must be YYYY-MM")
    try:
        day = date.fromisoformat(value + "-01")
    except ValueError as exc:
        raise ValueError("Month must be YYYY-MM") from exc
    if day.strftime("%Y-%m") != value:
        raise ValueError("Month must be YYYY-MM")
    return value


def months(start: str, end: str, max_months: int = 1) -> list[str]:
    start, end = validate_month(start), validate_month(end)
    if start > end:
        raise ValueError("start-month must not exceed end-month")
    if isinstance(max_months, bool) or max_months < 0:
        raise ValueError("max-months must be nonnegative")
    year, month = map(int, start.split("-"))
    result: list[str] = []
    while f"{year:04d}-{month:02d}" <= end:
        value = f"{year:04d}-{month:02d}"
        if EARLIEST_MONTH <= value <= LATEST_ADVERTISED_MONTH:
            result.append(value)
            if max_months and len(result) >= max_months:
                break
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


def raw_path(month: str) -> Path:
    validate_month(month)
    return private_path(ROOT / "raw" / "data" / f"ohlcv_{month}.parquet")


def output_path(month: str, session: str = "rth") -> Path:
    validate_month(month)
    if session not in {"rth", "all"}:
        raise ValueError("session must be rth or all")
    year, mm = month.split("-")
    return private_path(ROOT / "parquet" / f"session={session}" /
                        f"year={year}" / f"month={mm}" / f"ohlcv_{month}.parquet")


def quality_paths(out: Path) -> tuple[Path, Path]:
    """Separate invalid metadata from queryable price partitions."""
    base = private_path(ROOT / "parquet")
    relative = private_path(out).relative_to(base)
    quarantine = private_path(ROOT / "quality" / relative).with_name(
        Path(out).stem + ".quarantine.parquet")
    audit = quarantine.with_suffix(".json")
    return quarantine, audit


def _quote(path: Path) -> str:
    # All paths must remain in gitignored local_data, not arbitrary locations.
    return str(private_path(path)).replace("\\", "/").replace("'", "''")


def source_sql(raw: Path, session: str = "rth") -> str:
    if session not in {"rth", "all"}:
        raise ValueError("session must be rth or all")
    hours = ""
    if session == "rth":
        clock = "CAST(timezone('America/New_York', timestamp) AS TIME)"
        hours = f"WHERE {clock} >= TIME '09:30:00' AND {clock} < TIME '16:00:00'"
    # Bucket in UTC; US NYSE 09:30 and 16:00 boundaries both align to 5m.
    return f"""
WITH minutes AS (
  SELECT upper(trim(ticker)) AS ticker, timestamp, open, high, low, close, volume,
         epoch_ms(timestamp) - epoch_ms(timestamp) % {FIVE_MINUTES_MS} AS bucket_ms
  FROM read_parquet('{_quote(raw)}')
  {hours}
)
SELECT ticker AS symbol,
       CAST(timezone('America/New_York',to_timestamp(bucket_ms/1000.0)) AS DATE) AS trade_date_et,
       CAST(bucket_ms AS BIGINT) AS timestamp_ms,
       arg_min(open,timestamp) AS open,
       max(high) AS high,
       min(low) AS low,
       arg_max(close,timestamp) AS close,
       sum(volume) AS volume_shares,
       CAST(NULL AS BIGINT) AS transactions,
       count(*) AS source_minute_bars,
       count(DISTINCT epoch_ms(timestamp)) AS unique_source_minutes,
       count(*) FILTER (WHERE NOT coalesce({SOURCE_VALID}, false)) AS invalid_source_minutes
FROM minutes
GROUP BY ticker,bucket_ms
ORDER BY symbol,timestamp_ms
"""


def _open_duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise ValueError('Install lake dependencies: pip install -e ".[lake,hf]"') from exc
    return duckdb.connect(":memory:")


def _check_raw(cx, raw: Path) -> tuple[int, int]:
    """Reject structural errors and wide corruption; retain identifiable rare anomalies."""
    describe = cx.execute(f"DESCRIBE SELECT * FROM read_parquet('{_quote(raw)}')").fetchall()
    types = {row[0].lower(): row[1].upper() for row in describe}
    missing = REQUIRED - set(types)
    if missing:
        raise ValueError(f"Source Parquet missing columns: {', '.join(sorted(missing))}")
    if "WITH TIME ZONE" not in types["timestamp"] and "TIMESTAMPTZ" not in types["timestamp"]:
        raise ValueError("Source timestamp must be timezone-aware UTC (TIMESTAMPTZ)")
    total, invalid, unidentified = cx.execute(f"""
        SELECT count(*),
               count(*) FILTER (WHERE NOT coalesce({SOURCE_VALID}, false)),
               count(*) FILTER (WHERE NOT coalesce({IDENTIFIABLE}, false))
        FROM read_parquet('{_quote(raw)}')
    """).fetchone()
    total, invalid, unidentified = int(total), int(invalid), int(unidentified)
    if total == 0 or unidentified:
        raise ValueError(
            f"Source QC failed: {total} rows; {invalid} invalid OHLCV rows; "
            f"{unidentified} invalid ticker/timestamp rows (not safely quarantinable)"
        )
    if invalid > MAX_INVALID_SOURCE_ROWS or invalid / total > MAX_INVALID_SOURCE_FRACTION:
        raise ValueError(
            f"Source QC failed: {total} rows; {invalid} invalid OHLCV rows; "
            f"limits {MAX_INVALID_SOURCE_ROWS} rows / {MAX_INVALID_SOURCE_FRACTION:.4%}"
        )
    return total, invalid

def convert(raw: Path, out: Path, session: str = "rth") -> dict:
    """Quarantine whole bars containing invalid source rows or duplicated minutes.

    Preserve the original raw Parquet and never repair OHLCV or aggregate only
    the surviving minutes from a known-bad five-minute interval. Wide source
    corruption and excessive excluded buckets still fail closed.
    """
    if session not in {"rth", "all"}:
        raise ValueError("session must be rth or all")
    raw = private_path(Path(raw), exists=True)
    out = private_path(Path(out))
    if not raw.is_file() or raw.stat().st_size < 100:
        raise ValueError(f"Missing or empty source Parquet: {raw}")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = out.with_suffix(".parquet.aggregate.part")
    temp = out.with_suffix(".parquet.part")
    quarantine, audit = quality_paths(out)
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    qtemp = quarantine.with_suffix(".parquet.part")
    atemp = audit.with_suffix(".json.part")
    for candidate in (stage, temp, qtemp, atemp):
        candidate.unlink(missing_ok=True)
    cx = _open_duckdb()
    try:
        source_rows, invalid_source_rows = _check_raw(cx, raw)
        # One staged aggregate scan, with invalid-minute counts carried along.
        # Values from suspect buckets are NEVER selected into the final lake.
        cx.execute(f"COPY ({source_sql(raw,session)}) TO '{_quote(stage)}' "
                   "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)")
        bad = f"({AMBIGUOUS} OR invalid_source_minutes > 0)"
        (candidate_bars, ambiguous_buckets, invalid_source_buckets,
         excluded_buckets, invalid_session_rows) = cx.execute(f"""
            SELECT count(*),
                   count(*) FILTER (WHERE {AMBIGUOUS}),
                   count(*) FILTER (WHERE invalid_source_minutes > 0),
                   count(*) FILTER (WHERE {bad}),
                   coalesce(sum(invalid_source_minutes), 0)
            FROM read_parquet('{_quote(stage)}')
        """).fetchone()
        (candidate_bars, ambiguous_buckets, invalid_source_buckets,
         excluded_buckets, invalid_session_rows) = map(int, (
             candidate_bars, ambiguous_buckets, invalid_source_buckets,
             excluded_buckets, invalid_session_rows))
        if not candidate_bars:
            raise ValueError("5m QC failed: no session bars in source file")
        ratio = excluded_buckets / candidate_bars
        if excluded_buckets > MAX_AMBIGUOUS_BUCKETS or ratio > MAX_AMBIGUOUS_FRACTION:
            raise ValueError(
                f"5m QC failed: {candidate_bars} candidate bars, "
                f"{ambiguous_buckets} ambiguous buckets, "
                f"{invalid_source_buckets} invalid-source buckets, "
                f"{excluded_buckets} total excluded ({ratio:.6%}); "
                f"limit {MAX_AMBIGUOUS_FRACTION:.4%} or "
                f"{MAX_AMBIGUOUS_BUCKETS} buckets. Raw file preserved."
            )
        good = f"NOT {bad}"
        cx.execute(f"""
            COPY (
              SELECT symbol,trade_date_et,timestamp_ms,open,high,low,close,
                     volume_shares,transactions,source_minute_bars,unique_source_minutes
              FROM read_parquet('{_quote(stage)}') WHERE {good}
              ORDER BY symbol,timestamp_ms
            ) TO '{_quote(temp)}'
            (FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 100000)
        """)
        bars, first_ts, last_ts = cx.execute(f"""
            SELECT count(*),min(timestamp_ms),max(timestamp_ms)
            FROM read_parquet('{_quote(temp)}')
        """).fetchone()
        if not bars or int(bars) != candidate_bars - excluded_buckets:
            raise ValueError("5m QC failed: clean output count mismatch")
        if temp.stat().st_size < 100:
            raise ValueError("5m conversion produced a trivial/empty file")
        if excluded_buckets:
            # Identifiers/counts only. No quarantined OHLCV is queryable.
            # Overlap of duplicate-minute and invalid-source reasons is counted once.
            cx.execute(f"""
                COPY (
                  SELECT symbol,trade_date_et,timestamp_ms,source_minute_bars,
                         unique_source_minutes,invalid_source_minutes
                  FROM read_parquet('{_quote(stage)}') WHERE {bad}
                  ORDER BY symbol,timestamp_ms
                ) TO '{_quote(qtemp)}' (FORMAT PARQUET,COMPRESSION ZSTD)
            """)
        report = {
            "source_rows": source_rows,
            "invalid_source_rows": invalid_source_rows,
            "invalid_source_rows_in_session": invalid_session_rows,
            "max_invalid_source_rows": MAX_INVALID_SOURCE_ROWS,
            "max_invalid_source_fraction": MAX_INVALID_SOURCE_FRACTION,
            "candidate_bars_5m": candidate_bars,
            "bars_5m": int(bars),
            "excluded_ambiguous_5m_buckets": ambiguous_buckets,
            "excluded_invalid_source_5m_buckets": invalid_source_buckets,
            "excluded_total_5m_buckets": excluded_buckets,
            "excluded_ambiguous_fraction": round(ambiguous_buckets / candidate_bars, 9),
            "excluded_total_fraction": round(ratio, 9),
            "max_allowed_ambiguous_fraction": MAX_AMBIGUOUS_FRACTION,
            "first_timestamp_ms": int(first_ts),
            "last_timestamp_ms": int(last_ts),
            "quality_status": ("REVIEW_REQUIRED" if excluded_buckets or invalid_source_rows
                               else "NO_DUPLICATE_BUCKETS_DETECTED"),
            "quarantine": str(quarantine) if excluded_buckets else None,
            "output": str(out),
            "raw_source_preserved": True,
            "coverage_verified": False,
            "approved_for_model_training": False,
            "research_only": True,
            "live_execution_enabled": False,
        }
        with atemp.open("w",encoding="utf-8") as f:
            json.dump(report,f,indent=2,sort_keys=True)
        if excluded_buckets:
            os.replace(qtemp,quarantine)
        else:
            quarantine.unlink(missing_ok=True)
        os.replace(temp,out)
        os.replace(atemp,audit)
        return report
    finally:
        cx.close()
        for candidate in (stage,temp,qtemp,atemp):
            candidate.unlink(missing_ok=True)


def download(month: str, refresh: bool = False) -> Path:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise ValueError('Install Hugging Face support: pip install -e ".[lake,hf]"') from exc
    # local_dir places all downloaded blobs/caches under gitignored local_data.
    path = hf_hub_download(repo_id=REPO_ID, repo_type="dataset",
                           filename=f"data/ohlcv_{month}.parquet",
                           local_dir=str(private_path(ROOT / "raw")),
                           force_download=refresh)
    return private_path(Path(path), exists=True)


def backfill(start_month: str, end_month: str, *, max_months: int = 1,
             session: str = "rth", dry_run: bool = False, refresh: bool = False,
             remove_raw: bool = False, acknowledge_rights: bool = False,
             input_file: Path | None = None) -> dict:
    if session not in {"rth", "all"}:
        raise ValueError("session must be rth or all")
    chosen = months(start_month,end_month,max_months)
    if input_file is not None and len(chosen) != 1:
        raise ValueError("--input requires a single selected month")
    plan = {"provider": "Hugging Face", "repo_id": REPO_ID,
            "period_requested": [start_month,end_month], "months_selected": chosen,
            "advertised_archive": [EARLIEST_MONTH,LATEST_ADVERTISED_MONTH],
            "session": session, "adjustment": "UNVERIFIED_DO_NOT_TREAT_AS_ADJUSTED",
            "coverage_verified": False, "data_rights_verified": False,
            "research_only": True, "live_execution_enabled": False}
    if dry_run:
        return {**plan,"mode": "dry_run", "requires_network": input_file is None,
                "warning": "Only file names are planned; remote availability, data rights, and bar coverage are unverified"}
    if not chosen:
        raise ValueError("Requested period is outside the advertised dataset; no bars were fabricated")
    if input_file is None and not acknowledge_rights:
        raise ValueError("Public hosting is NOT a data license. Verify rights and use --acknowledge-rights to enable download")
    reports = []
    for number, month in enumerate(chosen, 1):
        out = output_path(month, session)
        if out.is_file() and out.stat().st_size > 100 and not refresh:
            reports.append({"month":month,"state":"existing","output":str(out)})
            continue
        raw = private_path(Path(input_file), exists=True) if input_file is not None else raw_path(month)
        if input_file is None and (refresh or not raw.is_file() or raw.stat().st_size < 100):
            raw = download(month, refresh=refresh)
        result = convert(raw,out,session)
        reports.append({"month":month,"state":"converted",**result})
        print(f"[hf-stock5m] {number}/{len(chosen)} {month} {result['bars_5m']:,} bars",file=sys.stderr,flush=True)
        if remove_raw and input_file is None:
            raw.unlink(missing_ok=True)
    return {**plan,"mode":"backfill","files":reports,
            "files_converted":sum(item['state']=='converted' for item in reports)}


def status() -> dict:
    base = private_path(ROOT / "parquet")
    files = sorted(base.glob("session=*/year=*/month=*/*.parquet"))
    audits = sorted(private_path(ROOT / "quality").glob(
        "session=*/year=*/month=*/*.quarantine.json"))
    quarantined = 0
    for file in audits:
        try:
            with file.open("r",encoding="utf-8") as stream:
                meta = json.load(stream)
                quarantined += int(meta.get("excluded_total_5m_buckets",
                                            meta["excluded_ambiguous_5m_buckets"]))
        except (ValueError,KeyError,TypeError, OSError) as exc:
            raise ValueError(f"Invalid Hugging Face QC audit: {file}") from exc
    return {"provider":"Hugging Face", "repo_id": REPO_ID,
            "parquet_files":len(files),
            "parquet_gib":round(sum(p.stat().st_size for p in files)/1073741824,3),
            "first_file":files[0].name if files else None,
            "last_file":files[-1].name if files else None,
            "quarantined_5m_buckets":quarantined,
            "quality_status":"REVIEW_REQUIRED" if quarantined else "NOT_CERTIFIED",
            "coverage_verified":False,"data_rights_verified":False,
            "research_only":True,"live_execution_enabled":False}
