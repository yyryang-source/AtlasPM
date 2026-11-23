"""Offline, source-isolated, point-in-time-*unverified* equity factor mart.

The factor mart contains one row per (source, session, symbol, trade_date).
It never merges adjusted daily closes with unadjusted intraday closes and
never grants training or trading eligibility. DuckDB reads/writes local
Parquet; SQLite is an optional independent source.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Any

from atlaspm.data.market_db import DB_PATH, private_path
from atlaspm.data.hf_stock5m import output_path as hf_output_path, validate_month

ROOT = Path("local_data/factors/v1")
VERSION = "equity_daily_v1"
SOURCES = ("hf", "sqlite")
FACTOR_DEFS = {
    "ret_1d": ("momentum", "Close-to-close return over prior observation; max 10-day gap"),
    "mom_5d": ("momentum", "5-observation close return; max 20-day gap"),
    "mom_21d": ("momentum", "21-observation close return; max 45-day gap"),
    "mom_63d": ("momentum", "63-observation close return; max 115-day gap"),
    "mom_126d": ("momentum", "126-observation close return; max 230-day gap"),
    "mom_252d": ("momentum", "252-observation close return; max 460-day gap"),
    "volatility_21d": ("risk", "Sample stddev of 21 valid trailing daily returns; unannualized"),
    "price_vs_sma_20d": ("trend", "Close / average of 20 observed closes minus one"),
    "volume_ratio_20d": ("liquidity", "Current volume / previous 20 observations' average volume"),
    "avg_dollar_volume_20d": ("liquidity", "20-observation mean(close * volume); intraday price adjustment unknown"),
    "intraday_return": ("price_action", "Daily close / daily open minus one (5m source only)"),
    "intraday_range": ("price_action", "Daily (high-low) / daily open (5m source only)"),
}
FEATURES = tuple(FACTOR_DEFS)
LOOKBACK_MONTHS = 36


def _month_first(month: str) -> date:
    return date.fromisoformat(validate_month(month) + "-01")


def _month_diff(a: date, b: date) -> int:
    return (a.year - b.year) * 12 + a.month - b.month


def _path(month: str, source: str, session: str, kind: str) -> Path:
    _month_first(month)
    if source not in SOURCES or session != "rth":
        raise ValueError("Supported sources: hf or sqlite; session must be rth")
    year, mm = month.split("-")
    name = ("daily" if kind == "daily" else "factors") + f"_{month}.parquet"
    return private_path(ROOT / kind / f"source={source}" / f"session={session}" /
                        f"year={year}" / f"month={mm}" / name)


def _quoted(path: Path) -> str:
    return "'" + str(private_path(path)).replace("\\", "/").replace("'", "''") + "'"


def _files_sql(paths: list[Path]) -> str:
    if not paths:
        raise ValueError("No historical daily files")
    return "read_parquet([" + ",".join(_quoted(p) for p in paths) + "], hive_partitioning=false)"


def _fingerprint(paths: list[Path]) -> str:
    evidence = []
    for p in paths:
        p = private_path(p, exists=True)
        stat = p.stat()
        evidence.append((str(p.relative_to(Path.cwd().resolve())).replace("\\", "/"),
                         stat.st_size, stat.st_mtime_ns))
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def _read_meta(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_meta(path: Path, obj: dict[str, Any]) -> None:
    tmp = private_path(path.with_suffix(path.suffix + ".part"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _db(memory: str, threads: int):
    try:
        import duckdb
    except ImportError as exc:
        raise ValueError('Install DuckDB: pip install -e ".[lake]"') from exc
    if threads < 1 or threads > 32:
        raise ValueError("threads must be between 1 and 32")
    if not memory or not memory[:-2].isdigit() or memory[-2:] not in ("MB", "GB"):
        raise ValueError("memory must be e.g. 2048MB or 4GB")
    tempdir = private_path(ROOT / "tmp")
    tempdir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    con.execute(f"SET memory_limit = '{memory}'")
    con.execute(f"SET threads = {threads}")
    con.execute(f"SET temp_directory = {_quoted(tempdir)}")
    return con


def _month_source(month: str, source: str, session: str, sqlite_db: Path) -> Path:
    if source == "hf":
        return private_path(hf_output_path(month, session))
    return private_path(sqlite_db)


def _daily_sql_hf(path: Path) -> str:
    s = f"read_parquet({_quoted(path)}, hive_partitioning=false)"
    return f"""
      SELECT upper(trim(symbol)) AS symbol, cast(trade_date_et AS DATE) AS trade_date,
        arg_min(open, timestamp_ms)::DOUBLE AS open,
        max(high)::DOUBLE AS high, min(low)::DOUBLE AS low,
        arg_max(close, timestamp_ms)::DOUBLE AS close,
        sum(volume_shares)::DOUBLE AS volume_shares,
        count(*)::BIGINT AS observed_5m_bars,
        sum(source_minute_bars)::BIGINT AS observed_1m_bars,
        max(timestamp_ms)::BIGINT AS last_observed_bar_ms
      FROM {s}
      GROUP BY 1,2 ORDER BY 1,2
    """


def _check_hf(con, path: Path) -> None:
    s = f"read_parquet({_quoted(path)}, hive_partitioning=false)"
    invalid = con.execute(f"""
      SELECT count(*) FILTER (WHERE NOT coalesce(
        symbol IS NOT NULL AND length(trim(symbol)) BETWEEN 1 AND 32
        AND trade_date_et IS NOT NULL AND timestamp_ms IS NOT NULL
        AND timestamp_ms % 300000 = 0
        AND cast(timezone('America/New_York', to_timestamp(timestamp_ms/1000.0)) AS DATE) = cast(trade_date_et AS DATE)
        AND cast(timezone('America/New_York', to_timestamp(timestamp_ms/1000.0)) AS TIME) >= TIME '09:30:00'
        AND cast(timezone('America/New_York', to_timestamp(timestamp_ms/1000.0)) AS TIME) < TIME '16:00:00'
        AND isfinite(open) AND isfinite(high) AND isfinite(low)
        AND isfinite(close) AND isfinite(volume_shares)
        AND open > 0 AND high >= greatest(open,close,low)
        AND low > 0 AND low <= least(open,close)
        AND close > 0 AND volume_shares >= 0
        AND source_minute_bars BETWEEN 1 AND 5
        AND unique_source_minutes = source_minute_bars, false)) FROM {s}
    """).fetchone()[0]
    if invalid:
        raise ValueError(f"HF input contains {invalid} invalid RTH bars; refuse factor construction")
    duplicate = con.execute(f"""
        SELECT 1 FROM {s} GROUP BY symbol,timestamp_ms HAVING count(*) > 1 LIMIT 1
    """).fetchone()
    if duplicate:
        raise ValueError("HF input contains duplicate (symbol,timestamp_ms) rows")


def _sqlite_temp_csv(month: str, database: Path, output: Path) -> int:
    import csv
    # Input stays read-only, and the staging file stays in gitignored local_data.
    uri = database.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute("""SELECT symbol,trade_date,adjusted_close FROM equity_daily
            WHERE substr(trade_date,1,7)=? ORDER BY symbol,trade_date""", (month,))
        count = 0
        with output.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["symbol", "trade_date", "close"])
            for symbol, day, close in rows:
                if not symbol or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
                    raise ValueError("Invalid sqlite adjusted-close input")
                writer.writerow((symbol, day, close))
                count += 1
        return count
    finally:
        con.close()


def _materialize_daily(con, month: str, source: str, session: str,
                       sqlite_db: Path, refresh: bool) -> tuple[Path, dict[str, Any]]:
    dest = _path(month, source, session, "daily")
    source_path = _month_source(month, source, session, sqlite_db)
    input_fp = _fingerprint([source_path])
    meta_path = dest.with_suffix(".json")
    meta = _read_meta(meta_path)
    if not refresh and dest.is_file() and meta and meta.get("input_fingerprint") == input_fp:
        return dest, meta
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = private_path(dest.with_suffix(".parquet.part"))
    csv_stage = private_path(ROOT / "tmp" / f"sqlite_{month}.csv.part")
    try:
        if source == "hf":
            _check_hf(con, source_path)
            sql = _daily_sql_hf(source_path)
        else:
            n = _sqlite_temp_csv(month, source_path, csv_stage)
            if not n:
                raise ValueError(f"No sqlite equity_daily records for {month}")
            sql = f"""SELECT symbol, cast(trade_date AS DATE) AS trade_date,
                NULL::DOUBLE AS open, NULL::DOUBLE AS high, NULL::DOUBLE AS low,
                close::DOUBLE AS close, NULL::DOUBLE AS volume_shares,
                NULL::BIGINT AS observed_5m_bars, NULL::BIGINT AS observed_1m_bars,
                NULL::BIGINT AS last_observed_bar_ms
                FROM read_csv({_quoted(csv_stage)}, header=true, auto_detect=false,
                    columns={{'symbol':'VARCHAR','trade_date':'VARCHAR','close':'DOUBLE'}})
                ORDER BY symbol,trade_date"""
        con.execute(f"COPY ({sql}) TO {_quoted(stage)} (FORMAT PARQUET, COMPRESSION ZSTD)")
        n, min_d, max_d = con.execute(f"SELECT count(*),min(trade_date),max(trade_date) FROM read_parquet({_quoted(stage)})").fetchone()
        if not n or str(min_d)[:7] != month or str(max_d)[:7] != month:
            raise ValueError("Daily derivation has no rows or dates outside requested month")
        meta = {"version": VERSION, "month": month, "source": source, "session": session,
                "input_fingerprint": input_fp, "rows": int(n),
                "price_adjustment": ("UNVERIFIED_HF_RAW" if source == "hf"
                                     else "SQLITE_ADJUSTED_CLOSE_VENDOR_UNVERIFIED"),
                "coverage_verified": False, "point_in_time_validated": False,
                "approved_for_model_training": False, "live_execution_enabled": False}
        os.replace(stage, dest)
        _write_meta(meta_path, meta)
        return dest, meta
    finally:
        stage.unlink(missing_ok=True)
        csv_stage.unlink(missing_ok=True)


def _factor_sql(paths: list[Path], month: str) -> str:
    source = _files_sql(paths)
    end = _month_first(month)
    year, mon = end.year + (end.month == 12), (end.month % 12) + 1
    end_date = f"{year:04d}-{mon:02d}-01"
    lags = [(1, 10), (5, 20), (21, 45), (63, 115), (126, 230), (252, 460)]
    lag_expr = ",\n".join(
        f"lag(close,{n}) OVER w AS close_{n}, lag(trade_date,{n}) OVER w AS day_{n}"
        for n, _ in lags)
    ret_expr = ",\n".join(
        f"CASE WHEN close_{n} > 0 AND date_diff('day',day_{n},trade_date) BETWEEN 1 AND {max_days} "
        f"THEN close/close_{n}-1 END AS {('ret_1d' if n==1 else 'mom_'+str(n)+'d')}"
        for n, max_days in lags)
    return f"""
    WITH lagged AS (
      SELECT *, {lag_expr},
        avg(close) OVER (PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20,
        count(close) OVER (PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20_n,
        avg(volume_shares) OVER (PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prior_volume_20,
        count(volume_shares) OVER (PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prior_volume_n,
        avg(close*volume_shares) OVER (PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS adv20,
        count(volume_shares) OVER (PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS adv20_n
      FROM {source} WINDOW w AS (PARTITION BY symbol ORDER BY trade_date)
    ), daily_ret AS (
      SELECT *, {ret_expr} FROM lagged
    ), rolled AS (
      SELECT *, stddev_samp(ret_1d) OVER (PARTITION BY symbol ORDER BY trade_date
          ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) AS sd21,
        count(ret_1d) OVER (PARTITION BY symbol ORDER BY trade_date
          ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) AS sd21_n
      FROM daily_ret
    )
    SELECT symbol, trade_date, close, volume_shares, observed_5m_bars,
      observed_1m_bars, last_observed_bar_ms,
      ret_1d, mom_5d, mom_21d, mom_63d, mom_126d, mom_252d,
      CASE WHEN sd21_n=21 THEN sd21 END AS volatility_21d,
      CASE WHEN sma20_n=20 AND sma20>0 THEN close/sma20-1 END AS price_vs_sma_20d,
      CASE WHEN prior_volume_n=20 AND prior_volume_20>0 THEN volume_shares/prior_volume_20 END AS volume_ratio_20d,
      CASE WHEN adv20_n=20 THEN adv20 END AS avg_dollar_volume_20d,
      CASE WHEN open>0 THEN close/open-1 END AS intraday_return,
      CASE WHEN open>0 THEN (high-low)/open END AS intraday_range
    FROM rolled WHERE trade_date >= DATE '{month}-01' AND trade_date < DATE '{end_date}'
    ORDER BY symbol,trade_date
    """


def catalog() -> dict:
    return {"schema": VERSION, "frequency": "1d", "factors": [
        {"name": name, "family": family, "definition": meaning,
         "available_at": "end_of_observed_day_only; vendor_lag_unverified"}
        for name, (family, meaning) in FACTOR_DEFS.items()],
        "research_only": True, "approved_for_model_training": False,
        "point_in_time_validated": False, "live_execution_enabled": False}


def build(month: str, *, source: str = "hf", session: str = "rth",
          sqlite_db: Path = DB_PATH, memory: str = "4GB", threads: int = 4,
          dry_run: bool = False, refresh: bool = False) -> dict:
    first = _month_first(month)
    dest = _path(month, source, session, "factors")
    source_path = _month_source(month, source, session, sqlite_db)
    if dry_run:
        return {"month": month, "source": source, "session": session,
                "source_file": str(source_path), "source_exists": source_path.is_file(),
                "factor_file": str(dest), "mode": "dry_run", "requires_network": False,
                "approved_for_model_training": False, "live_execution_enabled": False}
    if not source_path.is_file():
        raise ValueError(f"Missing source month {month}: {source_path}")
    con = _db(memory, threads)
    try:
        daily, _ = _materialize_daily(con, month, source, session, sqlite_db, refresh)
        directory = _path(month, source, session, "daily").parent.parent.parent
        candidates = sorted(directory.glob("year=*/month=*/*.parquet"))
        history = [p for p in candidates if 0 <= _month_diff(first, _month_first(p.stem[-7:])) <= LOOKBACK_MONTHS]
        if daily not in history:
            history.append(daily)
        history.sort()
        fingerprint = _fingerprint(history)
        meta_path = dest.with_suffix(".json")
        old = _read_meta(meta_path)
        if not refresh and dest.is_file() and old and old.get("history_fingerprint") == fingerprint and old.get("version") == VERSION:
            return {**old, "state": "existing", "factor_file": str(dest)}
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp = private_path(dest.with_suffix(".parquet.part"))
        try:
            con.execute(f"COPY ({_factor_sql(history,month)}) TO {_quoted(temp)} "
                        "(FORMAT PARQUET, COMPRESSION ZSTD)")
            stats = _audit_file(con, temp, month)
            if not stats["valid"]:
                raise ValueError("Factor output failed structural QC; previous output preserved")
            meta = {"version": VERSION, "month": month, "source": source, "session": session,
                    "history_fingerprint": fingerprint, "daily_input_months": len(history),
                    "factor_count": len(FEATURES), "rows": stats["rows"],
                    "non_null_factor_values": stats["non_null_factor_values"],
                    "source_price_adjustment": ("UNVERIFIED_HF_RAW" if source == "hf"
                                                else "SQLITE_ADJUSTED_CLOSE_VENDOR_UNVERIFIED"),
                    "missing_months_not_filled": True, "point_in_time_validated": False,
                    "coverage_verified": False, "data_rights_verified": False,
                    "approved_for_model_training": False,
                    "research_only": True, "live_execution_enabled": False}
            os.replace(temp, dest)
            _write_meta(meta_path, meta)
            return {**meta, "state": "built", "factor_file": str(dest)}
        finally:
            temp.unlink(missing_ok=True)
    finally:
        con.close()


def _audit_file(con, path: Path, month: str) -> dict[str, Any]:
    s = f"read_parquet({_quoted(path)}, hive_partitioning=false)"
    validity = " OR ".join(f"(NOT isfinite({col}))" for col in FEATURES)
    # isfinite(NULL) is NULL; coalesce makes missing warmup legitimate.
    rows, symbols, invalid, filled = con.execute(f"""
       SELECT count(*), count(DISTINCT symbol),
       count(*) FILTER (WHERE symbol IS NULL OR trade_date IS NULL
          OR cast(trade_date AS VARCHAR) NOT LIKE '{month}-%'
          OR close <= 0 OR NOT isfinite(close)
          OR coalesce({validity}, false)),
       { ' + '.join(f'count({c})' for c in FEATURES) }
       FROM {s}
    """).fetchone()
    dup = con.execute(f"SELECT 1 FROM {s} GROUP BY symbol,trade_date HAVING count(*)>1 LIMIT 1").fetchone()
    return {"rows": int(rows), "symbols": int(symbols),
            "invalid_rows": int(invalid), "duplicate_keys": bool(dup),
            "non_null_factor_values": int(filled),
            "valid": bool(rows) and invalid == 0 and not dup,
            "point_in_time_validated": False, "approved_for_model_training": False,
            "live_execution_enabled": False}


def audit(month: str, *, source: str = "hf", session: str = "rth",
          memory: str = "4GB", threads: int = 4) -> dict:
    path = _path(month, source, session, "factors")
    if not path.is_file():
        raise ValueError(f"Missing factor file: {path}")
    con = _db(memory, threads)
    try:
        return {"month": month, "source": source, "session": session,
                **_audit_file(con, path, month)}
    finally:
        con.close()


def status(*, source: str = "hf", session: str = "rth") -> dict:
    _path("2026-01", source, session, "factors")
    base = private_path(ROOT / "factors" / f"source={source}" / f"session={session}")
    paths = sorted(base.glob("year=*/month=*/*.parquet"))
    manifests = [_read_meta(p.with_suffix(".json")) for p in paths]
    return {"version": VERSION, "source": source, "session": session,
            "months": len(paths), "first_month": paths[0].stem[-7:] if paths else None,
            "last_month": paths[-1].stem[-7:] if paths else None,
            "rows_reported": sum(int(m.get("rows",0)) for m in manifests if m),
            "missing_manifests": sum(m is None for m in manifests),
            "root": str(base), "approved_for_model_training": False,
            "point_in_time_validated": False, "live_execution_enabled": False}


def query(symbol: str, *, source: str = "hf", session: str = "rth",
          start: str | None = None, end: str | None = None,
          limit: int = 20, memory: str = "4GB", threads: int = 4) -> dict:
    import re
    if not re.fullmatch(r"[A-Z][A-Z0-9.^-]{0,24}", symbol.upper()):
        raise ValueError("Invalid stock symbol")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if start:
        start = date.fromisoformat(start).isoformat()
    if end:
        end = date.fromisoformat(end).isoformat()
    if start and end and start > end:
        raise ValueError("start must not exceed end")
    _path("2026-01", source, session, "factors")
    base = private_path(ROOT / "factors" / f"source={source}" / f"session={session}")
    paths = sorted(p for p in base.glob("year=*/month=*/*.parquet")
                   if (not start or p.stem[-7:] >= start[:7])
                   and (not end or p.stem[-7:] <= end[:7]))
    if not paths:
        return {"symbol": symbol.upper(), "rows": [], "source": source, "session": session,
                "approved_for_model_training": False, "live_execution_enabled": False}
    con = _db(memory, threads)
    try:
        where = "symbol = ?"
        params: list[Any] = [symbol.upper()]
        if start:
            where += " AND trade_date >= ?"
            params.append(start)
        if end:
            where += " AND trade_date <= ?"
            params.append(end)
        result = con.execute(f"SELECT * FROM {_files_sql(paths)} WHERE {where} "
                             f"ORDER BY trade_date DESC LIMIT {limit}", params)
        cols = [d[0] for d in result.description]
        rows = [dict(zip(cols, [v.isoformat() if isinstance(v,date) else v for v in record]))
                for record in result.fetchall()]
        return {"symbol": symbol.upper(), "source": source, "session": session,
                "count": len(rows), "rows": rows,
                "point_in_time_validated": False, "approved_for_model_training": False,
                "live_execution_enabled": False}
    finally:
        con.close()


def batch(start_month: str, end_month: str, *, max_months: int = 0,
          source: str = "hf", session: str = "rth", sqlite_db: Path = DB_PATH,
          memory: str = "4GB", threads: int = 4,
          dry_run: bool = False, refresh: bool = False) -> dict:
    start, end = _month_first(start_month), _month_first(end_month)
    if start > end or max_months < 0:
        raise ValueError("Invalid batch month range or max-months")
    jobs = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        month = f"{y:04d}-{m:02d}"
        if _month_source(month, source, session, sqlite_db).is_file():
            jobs.append(month)
            if max_months and len(jobs) >= max_months:
                break
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    if dry_run:
        return {"mode": "dry_run", "months_with_source": jobs, "network": False,
                "approved_for_model_training": False, "live_execution_enabled": False}
    reports = [build(month, source=source, session=session, sqlite_db=sqlite_db,
                     memory=memory, threads=threads, refresh=refresh) for month in jobs]
    return {"months_processed": len(reports), "built": sum(r["state"] == "built" for r in reports),
            "skipped_existing": sum(r["state"] == "existing" for r in reports),
            "months": jobs, "approved_for_model_training": False,
            "live_execution_enabled": False}
