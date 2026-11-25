"""Local HF five-minute vs independent daily OHLCV reference (research only).

No implicit network, no automatic split adjustment or model approval.
A yfinance comparison is diagnostic rather than price-basis certification.
"""
from __future__ import annotations

import calendar
import csv
from datetime import date, timedelta
import json
from math import isfinite
import os
from pathlib import Path

from atlaspm.data import hf_stock5m
from atlaspm.data.hf_stock5m_audit import BENCHMARKS, _calendar
from atlaspm.data.market_db import private_path, SYMBOL_RE

SCHEMA = "atlaspm_hf_5m_daily_reconciliation_v1"


def _month_bounds(month: str) -> tuple[str, str]:
    year, mm = map(int, hf_stock5m.validate_month(month).split("-"))
    start = date(year, mm, 1)
    end = start + timedelta(days=calendar.monthrange(year, mm)[1])
    return start.isoformat(), end.isoformat()


def _validate_symbols(symbols: list[str] | tuple[str, ...] | None) -> list[str]:
    cleaned = list(dict.fromkeys(str(s).strip().upper() for s in
                                 (symbols if symbols is not None else BENCHMARKS)))
    if not cleaned or len(cleaned) > 20 or any(not SYMBOL_RE.fullmatch(s) for s in cleaned):
        raise ValueError("Select 1-20 valid symbols")
    return cleaned


def _numeric(label: str, value, positive: bool = False) -> float:
    try:
        n = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"Invalid {label}") from exc
    if not isfinite(n) or (n <= 0 if positive else n < 0):
        raise ValueError(f"Invalid {label}")
    return n


def _from_csv(path: Path, symbols: list[str], month: str) -> dict[tuple[str, str], tuple[float, float]]:
    path = private_path(path, exists=True)
    result = {}
    with path.open("r",newline="",encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"date","symbol","close","volume"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("Reference CSV requires date,symbol,close,volume")
        for i, row in enumerate(reader, 2):
            sym = str(row["symbol"] or "").strip().upper()
            if sym not in symbols:
                continue
            raw_date = str(row["date"] or "").strip()
            try:
                day = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise ValueError(f"Invalid reference date on row {i}") from exc
            if day.isoformat() != raw_date:
                raise ValueError(f"Invalid reference date on row {i}")
            if raw_date[:7] != month:
                continue
            key = (sym,raw_date)
            if key in result:
                raise ValueError(f"Duplicate reference day for {sym} {raw_date}")
            result[key] = (_numeric("reference close",row["close"],True),
                           _numeric("reference volume",row["volume"]))
    if not result:
        raise ValueError("No reference observations matched selected month/symbols")
    return result


def _from_yahoo(month: str, symbols: list[str]) -> dict[tuple[str, str], tuple[float, float]]:
    """Explicit network access only if the user supplied --fetch-yahoo."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ValueError('Install market extra: pip install -e ".[lake,calendar,market]"') from exc
    start,end = _month_bounds(month)
    observations = {}
    for symbol in symbols:
        try:
            frame = yf.Ticker(symbol).history(start=start,end=end,interval="1d",
                auto_adjust=False,actions=False)
        except Exception as exc:
            raise ValueError(f"Independent reference download failed for {symbol}: {type(exc).__name__}") from exc
        if frame is None or frame.empty:
            raise ValueError(f"Independent reference missing for {symbol}: Yahoo returned no daily rows")
        for index, row in frame.iterrows():
            day = index.date().isoformat()
            if day[:7] != month:
                continue
            key = (symbol,day)
            if key in observations:
                raise ValueError(f"Duplicate Yahoo daily observation: {symbol} {day}")
            observations[key] = (_numeric("Yahoo Close",row["Close"],True),
                                 _numeric("Yahoo Volume",row["Volume"]))
    return observations


def _hf_days(month: str, symbols: list[str]) -> list[tuple]:
    source = hf_stock5m.output_path(month,"rth")
    if not source.is_file():
        raise ValueError(f"Converted local RTH Parquet not found: {source}")
    cx = hf_stock5m._open_duckdb()
    try:
        cx.execute(f"CREATE TEMP VIEW candles AS SELECT * FROM read_parquet('{hf_stock5m._quote(source)}')")
        marks = ",".join("?" for _ in symbols)
        return cx.execute(f"""
            SELECT symbol, CAST(trade_date_et AS VARCHAR),
                arg_min(open,timestamp_ms) AS day_open,
                max(high) AS day_high, min(low) AS day_low,
                arg_max(close,timestamp_ms) AS day_close,
                sum(volume_shares) AS day_volume,
                min(timestamp_ms) AS first_ms, max(timestamp_ms) AS last_ms,
                count(*) AS five_minute_bars,
                count(*) FILTER (WHERE source_minute_bars IS DISTINCT FROM 5
                    OR unique_source_minutes IS DISTINCT FROM 5) AS partial_bars,
                arg_min(CASE WHEN source_minute_bars=5 AND unique_source_minutes=5 THEN 5 ELSE 0 END,
                    timestamp_ms) AS first_minutes,
                arg_max(CASE WHEN source_minute_bars=5 AND unique_source_minutes=5 THEN 5 ELSE 0 END,
                    timestamp_ms) AS last_minutes,
                count(DISTINCT timestamp_ms) AS unique_buckets,
                count(*) FILTER (WHERE timestamp_ms IS NULL OR timestamp_ms % 300000 <> 0
                    OR close IS NULL OR NOT isfinite(close) OR close <= 0
                    OR volume_shares IS NULL OR NOT isfinite(volume_shares)
                    OR volume_shares < 0) AS invalid_bars
            FROM candles
            WHERE symbol IN ({marks}) AND
                  CAST(trade_date_et AS VARCHAR) >= ? AND CAST(trade_date_et AS VARCHAR) < ?
            GROUP BY symbol,trade_date_et ORDER BY symbol,trade_date_et
        """, symbols+[month+"-01",_month_bounds(month)[1]]).fetchall()
    finally:
        cx.close()


def reconcile(month: str, *, symbols: list[str] | None = None,
              reference_csv: Path | None = None, fetch_yahoo: bool = False,
              close_tolerance_pct: float = 1.0, volume_tolerance_pct: float = 25.0,
              save: bool = True) -> dict:
    month = hf_stock5m.validate_month(month)
    symbols = _validate_symbols(symbols)
    if (reference_csv is None) == (not fetch_yahoo):
        raise ValueError("Supply exactly one of --reference-csv or --fetch-yahoo")
    close_tolerance_pct = _numeric("close tolerance",close_tolerance_pct)
    volume_tolerance_pct = _numeric("volume tolerance",volume_tolerance_pct)
    if close_tolerance_pct > 100 or volume_tolerance_pct > 1000:
        raise ValueError("Unreasonable comparison tolerances")
    schedule = {d.isoformat():(start,end) for d,start,end in _calendar(month)}
    local_rows = _hf_days(month,symbols)
    observations = (_from_yahoo(month,symbols) if fetch_yahoo else
                    _from_csv(Path(reference_csv),symbols,month))
    local = {(row[0],row[1]):row for row in local_rows}
    results = []
    high_close = []
    high_volume = []
    missing_edges = []
    unmatched = []
    for symbol in symbols:
        days = sorted(d for (sym,d) in observations if sym == symbol)
        compared = 0
        max_close = 0.0
        max_volume = 0.0
        missing_hf = 0
        edge_skipped = 0
        close_mismatches = 0
        volume_mismatches = 0
        partial_volume_days = 0
        invalid_local_days = 0
        volume_compared = 0
        zero_reference_volume_mismatches = 0
        for day in days:
            row = local.get((symbol,day))
            if row is None:
                missing_hf += 1
                if len(unmatched)<40:
                    unmatched.append({"symbol":symbol,"date":day,"reason":"MISSING_HF_DAY"})
                continue
            opening,closing = schedule.get(day,(None,None))
            # Reference calendar mismatch is never silently accepted.
            if opening is None:
                edge_skipped += 1
                if len(missing_edges)<40:
                    missing_edges.append({"symbol":symbol,"date":day,"reason":"NON_NYSE_DAY"})
                continue
            (sym,dt,op,high,low,close,vol,first,last,n,partial,first_min,last_min,
             unique_buckets,invalid_bars) = row
            if invalid_bars or unique_buckets!=n:
                invalid_local_days += 1
                if len(missing_edges)<40:
                    missing_edges.append({"symbol":symbol,"date":day,
                                          "reason":"INVALID_OR_DUPLICATE_LOCAL_BARS",
                                          "observed_bars":int(n)})
                continue
            if (int(first)!=opening or int(last)!=closing-300_000 or
                    int(first_min)!=5 or int(last_min)!=5):
                edge_skipped += 1
                if len(missing_edges)<40:
                    missing_edges.append({"symbol":symbol,"date":day,
                                          "reason":"FIRST_OR_LAST_FIVE_MINUTE_BAR_INCOMPLETE",
                                          "observed_bars":int(n)})
                continue
            ref_close,ref_volume = observations[(symbol,day)]
            diff = 100*abs(float(close)-ref_close)/ref_close
            complete_rth = int(n) == (closing-opening)//300_000 and int(partial) == 0
            if not complete_rth:
                partial_volume_days += 1
            volume_diff = (100*abs(float(vol)-ref_volume)/ref_volume
                           if complete_rth and ref_volume>0 else None)
            if complete_rth:
                if ref_volume==0:
                    if float(vol)==0:
                        volume_diff=0.0
                    else:
                        zero_reference_volume_mismatches+=1
                        if len(high_volume)<40:
                            high_volume.append({"symbol":symbol,"date":day,
                                                "hf_volume":float(vol),"reference_volume":0,
                                                "pct_difference":None,
                                                "reason":"ZERO_REFERENCE_NONZERO_LOCAL_VOLUME"})
                if volume_diff is not None:
                    volume_compared+=1
            compared += 1
            max_close=max(max_close,diff)
            if volume_diff is not None:
                max_volume=max(max_volume,volume_diff)
            if diff>close_tolerance_pct:
                close_mismatches += 1
                if len(high_close)<40:
                    high_close.append({"symbol":symbol,"date":day,
                                       "hf_close":round(float(close),6),
                                       "reference_close":round(ref_close,6),
                                       "pct_difference":round(diff,4),
                                       "hf_reference_price_ratio":round(float(close)/ref_close,5)})
            if volume_diff is not None and volume_diff>volume_tolerance_pct:
                volume_mismatches += 1
                if len(high_volume)<40:
                    high_volume.append({"symbol":symbol,"date":day,
                                        "hf_volume":round(float(vol),1),
                                        "reference_volume":round(ref_volume,1),
                                        "pct_difference":round(volume_diff,4)})
        missing_reference = sum(sym==symbol and (sym,day) not in observations
                                for sym,day in local)
        results.append({"symbol":symbol,"reference_days":len(days),
                        "compared_days":compared,
                        "volume_compared_days":volume_compared,
                        "invalid_local_bar_days":invalid_local_days,
                        "zero_reference_volume_mismatches":zero_reference_volume_mismatches,
                        "missing_hf_days":missing_hf,
                        "local_days_without_reference":missing_reference,
                        "skipped_incomplete_daily_boundaries":edge_skipped,
                        "close_outliers":close_mismatches,
                        "volume_outliers":volume_mismatches,
                        "incomplete_rth_volume_days":partial_volume_days,
                        "max_close_difference_pct":round(max_close,4) if compared else None,
                        "max_volume_difference_pct":round(max_volume,4) if volume_compared else None})
    compared_total = sum(x["compared_days"] for x in results)
    issues = []
    if not compared_total:
        issues.append("NO_COMPARABLE_DAYS")
    if any(x["close_outliers"] for x in results):
        issues.append("REFERENCE_CLOSE_DIVERGENCE")
    if any(x["volume_outliers"] or x["zero_reference_volume_mismatches"] for x in results):
        issues.append("REFERENCE_VOLUME_DIVERGENCE")
    if any(not x["compared_days"] for x in results):
        issues.append("REQUESTED_SYMBOL_WITHOUT_COMPARABLE_DAYS")
    if any(x["invalid_local_bar_days"] for x in results):
        issues.append("INVALID_OR_DUPLICATE_LOCAL_BARS")
    if any(x["incomplete_rth_volume_days"] for x in results):
        issues.append("PARTIAL_RTH_VOLUME_NOT_COMPARABLE")
    if any(x["skipped_incomplete_daily_boundaries"] for x in results):
        issues.append("INCOMPLETE_DAY_START_OR_END")
    if any(x["missing_hf_days"] or x["local_days_without_reference"] for x in results):
        issues.append("UNMATCHED_REFERENCE_DATES")
    report = {
        "schema":SCHEMA,"month":month,"session":"rth",
        "reference_source":("Yahoo Finance via local yfinance (explicit network request)" if fetch_yahoo
                            else "Local independent reference CSV"),
        "reference_basis":"UNVERIFIED_SPLIT_ADJUSTMENT_MAY_DIFFER",
        "selected_symbols":symbols,"per_symbol":results,
        "compared_symbol_days":compared_total,
        "volume_compared_symbol_days":sum(x["volume_compared_days"] for x in results),
        "close_tolerance_pct":close_tolerance_pct,
        "volume_tolerance_pct":volume_tolerance_pct,
        "close_outlier_examples":high_close,
        "volume_outlier_examples":high_volume,
        "incomplete_boundary_examples":missing_edges,
        "unmatched_day_examples":unmatched,
        "review_reasons":issues,
        "quality_status":"REVIEW_REQUIRED",
        "reference_prices_reconciled":not issues and compared_total>0,
        "corporate_actions_verified":False,
        "source_license_verified":False,
        "historical_universe_point_in_time_verified":False,
        "approved_for_model_training":False,"research_only":True,
        "live_execution_enabled":False,
        "warnings":[
            "Yahoo Close is generally split-adjusted; HF source adjustment status is not verified.",
            "Price discrepancies are diagnostic. Do not rescale price series automatically.",
            "Daily volume is only compared when all expected RTH 5m buckets contain five distinct source minutes; closing auctions can still differ.",
            "High benchmark agreement does not prove all 22k symbols are historically valid equities.",
            "Only one month's data are present; no training approval or out-of-sample signal validation.",
        ],
    }
    if save:
        dest=private_path(hf_stock5m.ROOT/"quality"/"reconciliations"/f"{month}.json")
        dest.parent.mkdir(parents=True,exist_ok=True)
        tmp=dest.with_suffix(".json.part")
        try:
            with tmp.open("w",encoding="utf-8") as f:
                json.dump(report,f,indent=2,sort_keys=True)
            os.replace(tmp,dest)
        finally:
            tmp.unlink(missing_ok=True)
        report["report_path"]=str(dest)
    return report
