"""Research-only Hugging Face 1-minute US equities -> local 5-minute Parquet.

The dataset is publicly downloadable, but upstream Finnhub-derived-data rights
are NOT established by public hosting. Never redistribute its price data.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import os
import sys

from atlaspm.data.market_db import private_path

REPO_ID = "mito0o852/OHLCV-1m"
EARLIEST_MONTH = "1992-01"
LATEST_ADVERTISED_MONTH = "2026-03"  # snapshot; NOT a promise of present-day bars
ROOT = Path("local_data/stock_5m/huggingface")
FIVE_MINUTES_MS = 300_000
REQUIRED = {"timestamp", "ticker", "open", "high", "low", "close", "volume"}


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
       count(DISTINCT epoch_ms(timestamp)) AS unique_source_minutes
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


def _check_raw(cx, raw: Path) -> int:
    describe = cx.execute(f"DESCRIBE SELECT * FROM read_parquet('{_quote(raw)}')").fetchall()
    types = {row[0].lower(): row[1].upper() for row in describe}
    missing = REQUIRED - set(types)
    if missing:
        raise ValueError(f"Source Parquet missing columns: {', '.join(sorted(missing))}")
    if "WITH TIME ZONE" not in types["timestamp"] and "TIMESTAMPTZ" not in types["timestamp"]:
        raise ValueError("Source timestamp must be timezone-aware UTC (TIMESTAMPTZ)")
    result = cx.execute(f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE NOT coalesce(
                    ticker IS NOT NULL AND length(trim(ticker)) BETWEEN 1 AND 32
                    AND timestamp IS NOT NULL
                    AND isfinite(open) AND isfinite(high) AND isfinite(low)
                    AND isfinite(close) AND isfinite(volume)
                    AND open > 0 AND high > 0 AND low > 0 AND close > 0
                    AND volume >= 0
                    AND low <= least(open,close) AND high >= greatest(open,close)
                    AND high >= low, false)) AS invalid
        FROM read_parquet('{_quote(raw)}')
    """).fetchone()
    total, invalid = int(result[0]), int(result[1])
    if total == 0 or invalid:
        raise ValueError(f"Source QC failed: {total} rows; {invalid} invalid OHLCV rows")
    return total


def convert(raw: Path, out: Path, session: str = "rth") -> dict:
    if session not in {"rth", "all"}:
        raise ValueError("session must be rth or all")
    raw = private_path(Path(raw), exists=True)
    out = private_path(Path(out))
    if not raw.is_file() or raw.stat().st_size < 100:
        raise ValueError(f"Missing or empty source Parquet: {raw}")
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(".parquet.part")
    temp.unlink(missing_ok=True)
    cx = _open_duckdb()
    try:
        total = _check_raw(cx, raw)
        cx.execute(f"COPY ({source_sql(raw,session)}) TO '{_quote(temp)}' "
                   "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)")
        bars, duplicates, min_ts, max_ts = cx.execute(f"""
            SELECT count(*),
                   count(*) FILTER (WHERE source_minute_bars > 5 OR source_minute_bars <> unique_source_minutes),
                   min(timestamp_ms), max(timestamp_ms)
            FROM read_parquet('{_quote(temp)}')
        """).fetchone()
        if not bars or duplicates:
            raise ValueError(f"5m QC failed: {bars} bars, {duplicates} duplicated-minute buckets")
        if temp.stat().st_size < 100:
            raise ValueError("5m conversion produced a trivial/empty file")
        os.replace(temp, out)
        return {"source_rows": total, "bars_5m": int(bars),
                "first_timestamp_ms": int(min_ts), "last_timestamp_ms": int(max_ts),
                "output": str(out)}
    finally:
        cx.close()
        temp.unlink(missing_ok=True)


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
    return {"provider":"Hugging Face", "repo_id": REPO_ID,
            "parquet_files":len(files),
            "parquet_gib":round(sum(p.stat().st_size for p in files)/1073741824,3),
            "first_file":files[0].name if files else None,
            "last_file":files[-1].name if files else None,
            "coverage_verified":False,"data_rights_verified":False,
            "research_only":True,"live_execution_enabled":False}
