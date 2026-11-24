"""AtlasPM factor evaluation V2: reproducible, *exploratory* daily cross-sectional diagnostics.

A factor known only after day T close is evaluated using the next session's
close as hypothetical entry and the (h+1)-th session's close as exit.
These are NOT executable fills, certified adjusted total returns, a backtest,
or approval for AI training. Source rights / ticker identities are unverified.
"""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from atlaspm.data import factor_db

VERSION = "atlaspm_factor_evaluation_v2"
ROOT = Path("local_data/factors/v2/evaluations")
HORIZONS = (1, 5, 21, 63)


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid ISO date: {value!r}") from exc


def _config(*, start: str, end: str, source: str, session: str,
            factors: list[str] | None, horizons: list[int] | None,
            holdout_start: str | None, min_symbols: int, min_dates: int,
            min_bars: int, min_minute_fraction: float,
            cost_bps_per_leg_roundtrip: float) -> dict:
    d0, d1 = _day(start), _day(end)
    if d0 > d1:
        raise ValueError("--start cannot be after --end")
    factor_db._path(d0.strftime("%Y-%m"), source, session, "factors")
    names = list(dict.fromkeys(factors or factor_db.FEATURES))
    if not names or any(n not in factor_db.FEATURES for n in names):
        raise ValueError(f"Factors must be from {', '.join(factor_db.FEATURES)}")
    hs = sorted(set(horizons or [5]))
    if not hs or any(isinstance(h, bool) or h not in HORIZONS for h in hs):
        raise ValueError("Supported horizons: 1, 5, 21, 63 sessions")
    if holdout_start is not None:
        ds = _day(holdout_start)
        if not d0 < ds <= d1:
            raise ValueError("--holdout-start must be after start and on/before end")
    if min_symbols < 5 or min_dates < 2:
        raise ValueError("--min-symbols >= 5 and --min-dates >= 2 required")
    if not 0 <= min_bars <= 78:
        raise ValueError("--min-bars must be from 0 to 78")
    if not math.isfinite(min_minute_fraction) or not 0 <= min_minute_fraction <= 1:
        raise ValueError("--min-minute-fraction must be 0..1")
    if not math.isfinite(cost_bps_per_leg_roundtrip) or not 0 <= cost_bps_per_leg_roundtrip <= 1000:
        raise ValueError("Cost must be between 0 and 1000 basis points")
    return {
        "start": d0.isoformat(), "end": d1.isoformat(),
        "source": source, "session": session, "factors": names, "horizons": hs,
        "holdout_start": holdout_start, "min_symbols": min_symbols,
        "min_dates": min_dates, "min_bars": min_bars,
        "min_minute_fraction": min_minute_fraction,
        "cost_bps_per_leg_roundtrip": cost_bps_per_leg_roundtrip,
    }


def _inputs(config: dict) -> tuple[list[Path], list[dict]]:
    # Include at most enough additional calendar time to mature the last label.
    # File selection never jumps to remote data or invents missing months.
    start = config["start"][:7]
    future = _day(config["end"]) + timedelta(days=3 * (max(config["horizons"]) + 1) + 14)
    end = future.strftime("%Y-%m")
    base = factor_db.private_path(factor_db.ROOT / "factors" /
                                  f"source={config['source']}" / f"session={config['session']}")
    paths = sorted(p for p in base.glob("year=*/month=*/*.parquet")
                   if start <= p.stem[-7:] <= end)
    if not paths:
        raise ValueError("No materialized factor months in requested period. Run factor-batch first.")
    manifests = []
    for path in paths:
        meta = factor_db._read_meta(path.with_suffix(".json"))
        month = path.stem[-7:]
        if not meta or meta.get("version") != factor_db.VERSION or meta.get("month") != month:
            raise ValueError(f"Missing or mismatched V1 factor manifest: {path}")
        if meta.get("source") != config["source"] or meta.get("session") != config["session"]:
            raise ValueError(f"Factor manifest source mismatch: {path}")
        if meta.get("approved_for_model_training") is not False:
            raise ValueError("Unexpected model-training flag in source manifest")
        manifests.append(meta)
    return paths, manifests


def _as_number(value: Any) -> float | None:
    if value is None:
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def _subperiod(daily: list[dict], min_dates: int, cost_bps: float) -> dict:
    rows = [r for r in daily if _as_number(r["rank_ic"]) is not None]
    if len(rows) < min_dates:
        return {
            "state": "INSUFFICIENT_DATED_IC", "dates": len(rows),
            "rank_ic_mean": None, "rank_ic_ir_unannualized": None,
            "q1_mean_forward_return": None, "q5_mean_forward_return": None,
            "q5_minus_q1_mean": None, "indicative_spread_after_fixed_cost": None,
            "mean_cohort_size": None,
        }
    ic = [float(r["rank_ic"]) for r in rows]
    std = stdev(ic) if len(ic) > 1 else None
    q1 = fmean(float(r["q1"]) for r in rows)
    q5 = fmean(float(r["q5"]) for r in rows)
    spread = fmean(float(r["q5"]) - float(r["q1"]) for r in rows)
    # Spread is not a realized daily P&L. Fixed costs are illustrative only:
    # a $1 long and $1 short each pay one per-leg round-trip cost.
    return {
        "state": "EXPLORATORY_UNVERIFIED",
        "dates": len(rows),
        "rank_ic_mean": fmean(ic),
        "rank_ic_ir_unannualized": fmean(ic) / std if std and std > 0 else None,
        "q1_mean_forward_return": q1,
        "q5_mean_forward_return": q5,
        "q5_minus_q1_mean": spread,
        "indicative_spread_after_fixed_cost": spread - (2 * cost_bps / 10000.0),
        "mean_cohort_size": fmean(int(r["cohort_size"]) for r in rows),
    }


def _eligible(alias: str, c: dict) -> str:
    if c["source"] == "sqlite":
        return "TRUE"  # Close-only SQLite does not have intraday completeness.
    min_bars = c["min_bars"]
    fraction = c["min_minute_fraction"]
    return (f"coalesce({alias}.observed_5m_bars >= {min_bars}"
            f" AND {alias}.observed_5m_bars > 0"
            f" AND {alias}.observed_1m_bars >= "
            f"{fraction} * 5 * {alias}.observed_5m_bars, false)")


def _forward_label_sql(horizon: int, config: dict, *, version: str) -> str:
    """Common global-calendar forward returns, independent of factor validity.

    V2 requires all three price rows to pass quality; V3 uses its already
    filtered base table. The raw calendar is never filtered by stock eligibility.
    """
    if type(horizon) is not int or horizon not in HORIZONS:
        raise ValueError("Invalid evaluation horizon")
    if version == "v2":
        raw = prices = "eval_prices"
        checks = " AND ".join(_eligible(alias, config) for alias in ("s","en","ex"))
    elif version == "v3":
        raw, prices, checks = "v3_raw", "v3_base", "TRUE"
    else:
        raise ValueError("Invalid factor label source")
    return f"""
    WITH days0 AS (
      SELECT trade_date, lag(trade_date) OVER (ORDER BY trade_date) AS prev_date
      FROM (SELECT DISTINCT trade_date FROM {raw})
    ), calendar AS (
      SELECT trade_date,
             row_number() OVER (ORDER BY trade_date)-1 AS seq,
             lead(trade_date,1) OVER (ORDER BY trade_date) AS entry_day,
             lead(trade_date,{horizon+1}) OVER (ORDER BY trade_date) AS exit_day,
             sum(CASE WHEN prev_date IS NOT NULL
                      AND date_diff('day',prev_date,trade_date)>7
                      THEN 1 ELSE 0 END)
               OVER (ORDER BY trade_date ROWS BETWEEN 1 FOLLOWING
                     AND {horizon+1} FOLLOWING) AS future_missing_periods
      FROM days0
    )
    SELECT s.symbol, s.trade_date, d.exit_day AS label_exit,
           ex.close::DOUBLE/en.close::DOUBLE-1 AS forward_ret
    FROM {prices} s
    JOIN calendar d ON s.trade_date=d.trade_date
    JOIN {prices} en ON en.symbol=s.symbol AND en.trade_date=d.entry_day
    JOIN {prices} ex ON ex.symbol=s.symbol AND ex.trade_date=d.exit_day
    WHERE s.trade_date BETWEEN ? AND ?
      AND d.seq % {horizon}=0
      AND d.future_missing_periods=0
      AND d.entry_day IS NOT NULL AND d.exit_day IS NOT NULL
      AND en.close>0 AND ex.close>0 AND {checks}
      AND isfinite(ex.close::DOUBLE/en.close::DOUBLE-1)
    """


def _materialize_forward_labels(con, horizon: int, config: dict, *, version: str) -> None:
    """Keep only one horizon's compact key/return relation in the database."""
    con.execute("DROP TABLE IF EXISTS factor_forward_labels")
    con.execute("CREATE TEMP TABLE factor_forward_labels AS " +
                _forward_label_sql(horizon, config, version=version),
                [config["start"], config["end"]])


def _dated_sql(name: str, horizon: int, config: dict, *, materialized: bool = False) -> str:
    if name not in factor_db.FEATURES:
        raise ValueError("Invalid evaluation factor")
    if type(horizon) is not int or horizon not in HORIZONS:
        raise ValueError("Invalid evaluation horizon")
    # Keep the standalone diagnostic SQL interface for parity tests. Normal
    # evaluation materializes the same labels once per horizon.
    labels = ("SELECT * FROM factor_forward_labels" if materialized else
              _forward_label_sql(horizon, config, version="v2"))
    return f"""
    WITH labels AS ({labels}), pairs AS (
      SELECT s.symbol, s.trade_date,
             s.{name}::DOUBLE AS factor_value,
             l.forward_ret AS future_return
      FROM eval_prices s JOIN labels l USING (symbol,trade_date)
      WHERE s.{name} IS NOT NULL AND isfinite(s.{name})
    ), ranked AS (
      SELECT *,
        rank() OVER (PARTITION BY trade_date ORDER BY factor_value)
           + (count(*) OVER (PARTITION BY trade_date,factor_value)-1)/2.0 AS rank_factor,
        rank() OVER (PARTITION BY trade_date ORDER BY future_return)
           + (count(*) OVER (PARTITION BY trade_date,future_return)-1)/2.0 AS rank_future,
        count(*) OVER (PARTITION BY trade_date) AS cohort_size
      FROM pairs WHERE isfinite(future_return)
    ), binned AS (
      SELECT *,
        least(5, 1+floor(5*(rank_factor-1)/cohort_size))::INTEGER AS quintile
      FROM ranked
    )
    SELECT trade_date, count(*) AS cohort_size,
        corr(rank_factor,rank_future) AS rank_ic,
        avg(future_return) FILTER (WHERE quintile=1) AS q1,
        avg(future_return) FILTER (WHERE quintile=2) AS q2,
        avg(future_return) FILTER (WHERE quintile=3) AS q3,
        avg(future_return) FILTER (WHERE quintile=4) AS q4,
        avg(future_return) FILTER (WHERE quintile=5) AS q5
    FROM binned GROUP BY trade_date
    HAVING count(*) >= {config['min_symbols']}
       AND count(*) FILTER(WHERE quintile=1)>0
       AND count(*) FILTER(WHERE quintile=5)>0
    ORDER BY trade_date
    """


def evaluate(*, start: str, end: str, source: str = "hf", session: str = "rth",
             factors: list[str] | None = None, horizons: list[int] | None = None,
             holdout_start: str | None = None, min_symbols: int = 30,
             min_dates: int = 8, min_bars: int = 60,
             min_minute_fraction: float = 0.85,
             cost_bps_per_leg_roundtrip: float = 20.0,
             memory: str = "4GB", threads: int = 4,
             exploratory: bool = False, save: bool = True) -> dict:
    c = _config(start=start, end=end, source=source, session=session,
                factors=factors, horizons=horizons, holdout_start=holdout_start,
                min_symbols=min_symbols, min_dates=min_dates, min_bars=min_bars,
                min_minute_fraction=min_minute_fraction,
                cost_bps_per_leg_roundtrip=cost_bps_per_leg_roundtrip)
    paths, metas = _inputs(c)
    outline = {
        "schema": VERSION, "config": c,
        "available_factor_months": [p.stem[-7:] for p in paths],
        "input_months": len(paths),
        "input_provenance": "factor_db_v1",
        "source_adjustment": sorted(set(m["source_price_adjustment"] for m in metas)),
        "signal_timestamp": "after T close; vendor availability unverified",
        "hypothetical_entry": "T+1 global-market-session close",
        "hypothetical_exit": "T+h+1 global-market-session close",
        "nonoverlapping_session_stride": "global-session-index mod h == 0",
        "calendar_gap_max_days": 7,
        "historic_security_identity_verified": False,
        "corporate_actions_verified": False,
        "data_rights_verified": False,
        "point_in_time_validated": False,
        "approved_for_model_training": False,
        "live_execution_enabled": False,
        "research_only": True,
    }
    if not exploratory:
        return {**outline, "mode": "plan_only", "requires_network": False,
                "reason": "Add --exploratory to compute unverified research diagnostics. "
                          "This does not authorize ML training or trading."}
    con = factor_db._db(memory, threads)
    try:
        select_columns = ", ".join(c["factors"])
        con.execute("CREATE TEMP TABLE eval_prices AS "
                    "SELECT symbol,trade_date,close,observed_5m_bars,observed_1m_bars,"
                    f"{select_columns} FROM {factor_db._files_sql(paths)}")
        # Audit all participating source rows once before using them as labels.
        invalid = con.execute("""SELECT count(*) FROM eval_prices
            WHERE symbol IS NULL OR trade_date IS NULL OR close IS NULL
               OR close<=0 OR NOT isfinite(close)""").fetchone()[0]
        if invalid:
            raise ValueError(f"Factor evaluation refused {invalid} invalid close/key rows")
        duplicate = con.execute("""SELECT 1 FROM eval_prices
           GROUP BY symbol,trade_date HAVING count(*)>1 LIMIT 1""").fetchone()
        if duplicate:
            raise ValueError("Duplicate symbol/date across factor inputs")
        reports = []
        for horizon in c["horizons"]:
            _materialize_forward_labels(con, horizon, c, version="v2")
            for name in c["factors"]:
                result = con.execute(_dated_sql(name, horizon, c, materialized=True))
                columns = [col[0] for col in result.description]
                daily = [dict(zip(columns, row))
                         for row in result.fetchall()]
                for day in daily:
                    day["trade_date"] = day["trade_date"].isoformat()
                holdout = c["holdout_start"]
                full = _subperiod(daily, c["min_dates"], c["cost_bps_per_leg_roundtrip"])
                entry = {
                    "factor": name, "horizon_sessions": horizon,
                    "dated_cohorts_found": len(daily),
                    "full_period": full,
                    "chronological_diagnostic": (
                        {"pre_holdout": _subperiod(
                            [d for d in daily if d["trade_date"] < holdout],
                            c["min_dates"], c["cost_bps_per_leg_roundtrip"]),
                         "holdout": _subperiod(
                            [d for d in daily if d["trade_date"] >= holdout],
                            c["min_dates"], c["cost_bps_per_leg_roundtrip"]),
                         "holdout_start": holdout,
                         "not_independent_if_selected_post_hoc": True}
                        if holdout else None),
                    "daily_ic": [
                        {"date": d["trade_date"], "cohort_size": int(d["cohort_size"]),
                         "rank_ic": _as_number(d["rank_ic"])}
                        for d in daily],
                }
                reports.append(entry)
        outcome = {
            **outline, "mode": "exploratory_diagnostics",
            "factor_horizon_results": reports,
            "no_verified_alpha_claim": True,
            "not_a_tradable_backtest": True,
            "transaction_cost_note": "Fixed per-leg round-trip bps scenario only; "
                "no spread/slippage/borrow/turnover model or portfolio simulation.",
            "rank_ic_ir_note": "Mean daily (non-overlapping decision-cohort) Rank IC / "
                "sample standard deviation; not annualized.",
            "holdout_note": "Time partition is descriptive, not independent validation "
                "if factor definitions/holdout were selected after seeing outcomes.",
        }
        if save:
            digest = sha256(json.dumps(c, sort_keys=True).encode("utf-8")).hexdigest()[:12]
            path = factor_db.private_path(ROOT / f"evaluation_{c['start']}_{c['end']}_{digest}.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = factor_db.private_path(path.with_suffix(".json.part"))
            try:
                tmp.write_text(json.dumps(outcome, indent=2, sort_keys=True,
                                          allow_nan=False), encoding="utf-8")
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
            # CLI output can remain small, full dated series is in local file.
            outcome["report_path"] = str(path)
        for r in reports:
            r["daily_ic"] = r["daily_ic"][:10]
        return outcome
    finally:
        con.close()
