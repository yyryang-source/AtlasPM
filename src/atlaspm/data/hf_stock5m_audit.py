"""Offline monthly HF 5m archive audit: no downloads, broker or training.

This is descriptive QC only. Complete historical point-in-time coverage,
corporate actions, trading permissions, and data rights remain UNVERIFIED.
"""
from __future__ import annotations

import calendar
from datetime import date
import json
import os

from atlaspm.data import hf_stock5m
from atlaspm.data.market_db import private_path

BENCHMARKS = ("AAPL", "MSFT", "NVDA", "SPY", "QQQ", "GOOGL", "AMZN", "META")
SCHEMA = "atlaspm_hf_5m_research_qc_v2"


def _calendar(month: str):
    try:
        import pandas_market_calendars as mcal
    except ImportError as exc:
        raise ValueError('Install NYSE calendar support: pip install -e ".[lake,calendar,hf]"') from exc
    year, mm = map(int, hf_stock5m.validate_month(month).split("-"))
    first = date(year, mm, 1)
    last = date(year, mm, calendar.monthrange(year, mm)[1])
    schedule = mcal.get_calendar("NYSE").schedule(start_date=first.isoformat(),
                                                   end_date=last.isoformat())
    dates = []
    for index, row in schedule.iterrows():
        opening = row["market_open"]
        closing = row["market_close"]
        start_ms = int(opening.timestamp() * 1000)
        end_ms = int(closing.timestamp() * 1000)
        if end_ms <= start_ms or start_ms % 60_000 or end_ms % 60_000:
            raise ValueError("Invalid NYSE market session bounds")
        # Historical NYSE sessions include 09:31/09:32/09:33 late opens and
        # even 15:56 closes. A duration need not be divisible by five minutes.
        # The lake aggregates fixed UTC 5m buckets, so partial boundary
        # buckets require separate scrutiny instead of rejecting the calendar.
        dates.append((index.date(), start_ms, end_ms))
    if not dates:
        raise ValueError("NYSE calendar produced zero sessions")
    return dates


def audit(month: str, session: str = "rth", save: bool = True) -> dict:
    """Inspect one already-converted Parquet month with a NYSE calendar.

    This function makes zero network calls. --session all is supported,
    but strict NYSE session checks only apply to regular-trading-hours data.
    """
    hf_stock5m.validate_month(month)
    if session not in ("rth", "all"):
        raise ValueError("session must be rth or all")
    source = hf_stock5m.output_path(month, session)
    if not source.is_file():
        raise ValueError(f"Local 5m Parquet not found: {source}. Run hf-5m-backfill first")
    sessions = _calendar(month)
    width = hf_stock5m.FIVE_MINUTES_MS
    # Include every fixed UTC 5m bucket overlapping the [open, close)
    # exchange session. This preserves the partial first/last bucket.
    calendar_rows = [
        (day, opening, closing, (opening // width) * width,
         ((closing + width - 1) // width) * width)
        for day, opening, closing in sessions
    ]
    expected = sum((last - first) // width for _, _, _, first, last in calendar_rows)
    special_boundaries = [
        {"date": day.isoformat(), "market_open_ms": opening,
         "market_close_ms": closing,
         "late_open_partial_bucket": opening % width != 0,
         "early_close_partial_bucket": closing % width != 0}
        for day, opening, closing, _, _ in calendar_rows
        if opening % width or closing % width
    ]
    cx = hf_stock5m._open_duckdb()
    try:
        cx.execute("""
            CREATE TEMP TABLE nyse(
                trade_date DATE, open_ms BIGINT, close_ms BIGINT,
                first_bucket_ms BIGINT, last_bucket_end_ms BIGINT)
        """)
        cx.executemany("INSERT INTO nyse VALUES (?,?,?,?,?)", calendar_rows)
        cx.execute(f"CREATE TEMP VIEW bars AS SELECT * FROM read_parquet('{hf_stock5m._quote(source)}')")
        required = {
            "symbol", "trade_date_et", "timestamp_ms", "open", "high", "low", "close",
            "volume_shares", "source_minute_bars", "unique_source_minutes",
        }
        cols = {r[0].lower() for r in cx.execute("DESCRIBE bars").fetchall()}
        if required - cols:
            raise ValueError("Missing 5m output columns: " + ",".join(sorted(required - cols)))
        stats = cx.execute("""
            SELECT count(*) AS bars,
                count(DISTINCT symbol) AS tickers,
                count(DISTINCT trade_date_et) AS observed_dates,
                min(trade_date_et) AS first_trade_date,
                max(trade_date_et) AS last_trade_date,
                min(timestamp_ms) AS first_timestamp,
                max(timestamp_ms) AS last_timestamp,
                coalesce(sum(source_minute_bars),0) AS minute_inputs,
                count(*) FILTER (WHERE source_minute_bars < 5) AS sparse_buckets,
                count(*) FILTER (WHERE source_minute_bars = 5) AS full_buckets,
                count(*) FILTER (WHERE NOT coalesce(
                     symbol IS NOT NULL AND length(trim(symbol)) > 0
                     AND timestamp_ms IS NOT NULL AND timestamp_ms % 300000 = 0
                     AND trade_date_et IS NOT NULL
                     AND isfinite(open) AND isfinite(high) AND isfinite(low)
                     AND isfinite(close) AND isfinite(volume_shares)
                     AND open > 0 AND high > 0 AND low > 0 AND close > 0
                     AND volume_shares >= 0
                     AND low <= least(open,close) AND high >= greatest(open,close)
                     AND source_minute_bars BETWEEN 1 AND 5
                     AND source_minute_bars = unique_source_minutes
                     AND trade_date_et = CAST(timezone('America/New_York',
                                                   to_timestamp(timestamp_ms/1000.0)) AS DATE),
                  false)) AS invalid_bars
            FROM bars
        """).fetchone()
        (bars, symbols, observed_dates, first_date, last_date, first_ms,
         last_ms, actual_minute_inputs, sparse_buckets, full_buckets, invalid_bars) = stats
        duplicate_keys = cx.execute("""
            SELECT coalesce(sum(c - 1),0)
            FROM (
                SELECT count(*) AS c
                FROM bars GROUP BY symbol,timestamp_ms HAVING count(*) > 1
            )
        """).fetchone()[0]
        non_session = None
        boundary_bars = None
        boundary_minute_overflow = None
        if session == "rth":
            # A 5m bar is named by its UTC bucket START. For irregular
            # opening/closing minutes the bucket may start before the actual
            # exchange session yet still contain valid trades.
            non_session = cx.execute("""
                SELECT count(*)
                FROM bars a LEFT JOIN nyse s ON a.trade_date_et=s.trade_date
                WHERE s.trade_date IS NULL OR a.timestamp_ms < s.first_bucket_ms
                   OR a.timestamp_ms >= s.last_bucket_end_ms
            """).fetchone()[0]
            # Avoid a second full monthly Parquet scan on the many months
            # whose entire NYSE schedule already lies on regular 5m edges.
            boundary_bars, boundary_minute_overflow = 0, 0
            if special_boundaries:
                boundary_bars, boundary_minute_overflow = cx.execute(f"""
                    SELECT count(*),
                           count(*) FILTER (
                               WHERE a.source_minute_bars > floor(
                                 (least(a.timestamp_ms + {width}, s.close_ms)
                                  - greatest(a.timestamp_ms, s.open_ms)) / 60000.0))
                    FROM bars a JOIN nyse s ON a.trade_date_et=s.trade_date
                    WHERE a.timestamp_ms < s.close_ms
                      AND a.timestamp_ms + {width} > s.open_ms
                      AND (a.timestamp_ms < s.open_ms
                           OR a.timestamp_ms + {width} > s.close_ms)
                """).fetchone()
        day_rows = cx.execute("""
            SELECT trade_date_et, count(*), count(DISTINCT symbol)
            FROM bars GROUP BY trade_date_et ORDER BY trade_date_et
        """).fetchall()
        daily = [{"date": d.isoformat(), "bars_5m": int(n), "symbols": int(t)}
                 for d, n, t in day_rows]
        date_set = {d.isoformat() for d, *_ in day_rows}
        expected_days = [d.isoformat() for d, *_ in sessions]
        unexpected_days = sorted(date_set - set(expected_days))
        missing_days = sorted(set(expected_days) - date_set)
        # A five-minute slot without a trade is not proof of missing source data.
        # Benchmark coverage is an indicator to investigate, not automatic imputation.
        placeholders = ",".join("?" for _ in BENCHMARKS)
        benchmarks = cx.execute(f"""
            SELECT symbol, count(*), count(DISTINCT trade_date_et),
                   sum(source_minute_bars), count(*) FILTER (WHERE source_minute_bars = 5)
            FROM bars WHERE symbol IN ({placeholders})
            GROUP BY symbol ORDER BY symbol
        """, list(BENCHMARKS)).fetchall()
        bench = {row[0]: row for row in benchmarks}
        b_report = []
        for sym in BENCHMARKS:
            row = bench.get(sym)
            count = int(row[1]) if row else 0
            count_days = int(row[2]) if row else 0
            used_minutes = int(row[3]) if row else 0
            fully_observed = int(row[4]) if row else 0
            b_report.append({
                "symbol": sym, "five_minute_bars": count,
                "observed_sessions": count_days,
                "expected_nyse_sessions": len(sessions),
                "expected_rth_five_minute_slots": int(expected),
                "rth_slot_coverage_pct": (round(100 * count / expected, 3)
                                          if session == "rth" else None),
                "full_five_source_minutes": fully_observed,
                "input_minute_rows": used_minutes,
            })
        quarantine, audit_metadata = hf_stock5m.quality_paths(source)
        exclusion = None
        total_excluded = None
        invalid_source_buckets = 0
        invalid_source_rows = 0
        if audit_metadata.is_file():
            with audit_metadata.open("r",encoding="utf-8") as f:
                metadata = json.load(f)
            exclusion = int(metadata["excluded_ambiguous_5m_buckets"])
            total_excluded = int(metadata.get("excluded_total_5m_buckets", exclusion))
            invalid_source_buckets = int(metadata.get("excluded_invalid_source_5m_buckets", 0))
            invalid_source_rows = int(metadata.get("invalid_source_rows", 0))
        problems = []
        if not bars:
            problems.append("NO_BARS")
        if invalid_bars:
            problems.append(f"INVALID_5M_BARS:{invalid_bars}")
        if duplicate_keys:
            problems.append(f"DUPLICATE_SYMBOL_TIMESTAMPS:{duplicate_keys}")
        if session == "rth" and non_session:
            problems.append(f"OUTSIDE_NYSE_SESSIONS:{non_session}")
        if session == "rth" and unexpected_days:
            problems.append("UNEXPECTED_TRADING_DATES")
        if boundary_minute_overflow:
            # More observed 1m rows than calendar-open minutes in a partial
            # 5m bucket cannot be fully explained by exchange RTH alone.
            problems.append(f"NYSE_BOUNDARY_MINUTE_COUNT_EXCEEDED:{boundary_minute_overflow}")
        if exclusion is None:
            problems.append("NO_CONVERSION_QC_METADATA")
        if total_excluded and not quarantine.is_file():
            problems.append("MISSING_QUARANTINE_FILE")
        # Missing whole NYSE days or benchmarks are review issues, not invalid
        # OHLC records. An absence might be a data/vendor or market-halt issue.
        review = []
        if missing_days:
            review.append("MISSING_WHOLE_NYSE_SESSIONS")
        if any(x["five_minute_bars"] == 0 for x in b_report):
            review.append("MISSING_BENCHMARK_SYMBOLS")
        if exclusion:
            review.append("QUARANTINED_AMBIGUOUS_5M_BUCKETS")
        if invalid_source_buckets:
            review.append("QUARANTINED_INVALID_SOURCE_5M_BUCKETS")
        if invalid_source_rows:
            review.append("RAW_SOURCE_OHLCV_ANOMALIES")
        if session == "rth" and special_boundaries:
            review.append("HISTORICAL_NYSE_PARTIAL_5M_SESSION_BOUNDARIES")
        if session == "rth" and boundary_bars:
            review.append("PARTIAL_NYSE_BOUNDARY_5M_BARS_REQUIRE_SOURCE_REVIEW")
        if session == "all":
            review.append("EXTENDED_HOURS_NOT_VERIFIED_AGAINST_EXCHANGE_SCHEDULE")
        status = ("STRUCTURAL_QC_FAILED" if problems else "REVIEW_REQUIRED")
        report = {
            "schema": SCHEMA, "provider": hf_stock5m.REPO_ID,
            "month": month, "session": session, "source": str(source),
            "five_minute_bars": int(bars),
            "unique_symbols": int(symbols),
            "observed_trade_dates": int(observed_dates),
            "first_trade_date": first_date.isoformat() if first_date else None,
            "last_trade_date": last_date.isoformat() if last_date else None,
            "first_timestamp_ms": int(first_ms) if first_ms is not None else None,
            "last_timestamp_ms": int(last_ms) if last_ms is not None else None,
            "input_minute_rows_in_session": int(actual_minute_inputs),
            "sparse_five_minute_buckets": int(sparse_buckets),
            "sparse_bucket_pct": round(100 * sparse_buckets / bars, 4) if bars else None,
            "complete_five_minute_buckets": int(full_buckets),
            "invalid_five_minute_bars": int(invalid_bars),
            "duplicate_symbol_timestamp_rows": int(duplicate_keys),
            "out_of_nyse_session_bars": int(non_session) if non_session is not None else None,
            "nyse_expected_sessions": len(sessions),
            "nyse_expected_rth_slots_per_liquid_symbol": int(expected),
            "non_5m_aligned_nyse_sessions": special_boundaries,
            "partial_nyse_boundary_5m_bars": (
                int(boundary_bars) if boundary_bars is not None else None),
            "nyse_boundary_minute_count_exceeded": (
                int(boundary_minute_overflow)
                if boundary_minute_overflow is not None else None),
            "missing_whole_trading_dates": missing_days,
            "unexpected_trading_dates": unexpected_days,
            "daily_summary": daily,
            "benchmark_coverage": b_report,
            "ambiguous_buckets_quarantined": exclusion,
            "invalid_source_5m_buckets_quarantined": invalid_source_buckets,
            "total_5m_buckets_quarantined": total_excluded,
            "invalid_raw_source_rows": invalid_source_rows,
            "structural_issues": problems,
            "review_reasons": review,
            "quality_status": status,
            "coverage_verified": False,
            "historical_point_in_time_membership_verified": False,
            "corporate_actions_verified": False,
            "data_rights_verified": False,
            "approved_for_model_training": False,
            "research_only": True,
            "live_execution_enabled": False,
            "warnings": [
                "No automatic approval for AI model training or portfolio/order workflows.",
                "Missing 5m slots may reflect low liquidity, not a vendor outage.",
                "Non-aligned NYSE open/close times require boundary-minute review; this 5m lake does not reveal exact included 1m timestamps.",
                "Adjusted/raw split and dividend semantics require independent verification.",
                "Historical company identities, IPO/delist dates and universe are not point-in-time certified.",
                "Source data rights and secondary redistribution permission are not established.",
            ],
        }
        if save:
            dest = private_path(hf_stock5m.ROOT / "quality" / "audits" /
                                f"session={session}" / f"{month}.json")
            dest.parent.mkdir(parents=True,exist_ok=True)
            tmp = dest.with_suffix(".json.part")
            try:
                with tmp.open("w",encoding="utf-8") as f:
                    json.dump(report,f,indent=2,sort_keys=True)
                os.replace(tmp,dest)
            finally:
                tmp.unlink(missing_ok=True)
            report["audit_report_path"] = str(dest)
        return report
    finally:
        cx.close()
