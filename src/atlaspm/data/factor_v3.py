"""AtlasPM V3 offline cross-sectional factor laboratory (research-only).

No vendor rights, corporate actions, historical identifiers or training eligibility
are certified. V3 scores are descriptive diagnostics and MUST NOT feed live orders.
"""
from __future__ import annotations

import csv
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import re
from statistics import fmean, stdev

from atlaspm.data import factor_db, factor_eval

VERSION = "atlaspm_factor_lab_v3"
ROOT = Path("local_data/factors/v3")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.^-]{0,31}$")


def _validate(*, start: str, end: str, factors: list[str] | None,
              horizon: int, holdout_start: str | None,
              weighting: str, neutralize_industry: bool, industry_csv: Path | None,
              min_group_size: int, winsor_tail: float, redundancy_threshold: float,
              min_symbols: int, min_dates: int, min_bars: int,
              min_minute_fraction: float, source: str, session: str) -> dict:
    d0, d1 = factor_eval._day(start), factor_eval._day(end)
    if d0 > d1:
        raise ValueError("Start must not be after end")
    factor_db._path(d0.strftime("%Y-%m"), source, session, "factors")
    names = list(dict.fromkeys(factors if factors is not None else factor_db.FEATURES))
    if not names or any(n not in factor_db.FEATURES for n in names):
        raise ValueError("Unknown or empty factor selection")
    if horizon not in factor_eval.HORIZONS:
        raise ValueError("Horizon must be 1, 5, 21 or 63")
    if weighting not in ("equal", "ic_train"):
        raise ValueError("Weighting must be equal or ic_train")
    if holdout_start is not None:
        d = factor_eval._day(holdout_start)
        if not d0 < d <= d1:
            raise ValueError("holdout-start must be inside (start,end]")
    if weighting == "ic_train" and holdout_start is None:
        raise ValueError("ic_train requires --holdout-start and matured training labels")
    if neutralize_industry != (industry_csv is not None):
        raise ValueError("Industry neutralization requires --neutralize-industry and --industry-csv together")
    if not 2 <= min_group_size <= 1000:
        raise ValueError("min-group-size must be between 2 and 1000")
    if not math.isfinite(winsor_tail) or not 0 <= winsor_tail < 0.25:
        raise ValueError("winsor-tail must be from 0 up to (not including) 0.25")
    if not math.isfinite(redundancy_threshold) or not 0.5 <= redundancy_threshold <= 1:
        raise ValueError("redundancy-threshold must be 0.5..1")
    if min_symbols < 5 or min_dates < 2:
        raise ValueError("Require min-symbols >=5 and min-dates >=2")
    if not 0 <= min_bars <= 78:
        raise ValueError("min-bars must be 0..78")
    if not math.isfinite(min_minute_fraction) or not 0 <= min_minute_fraction <= 1:
        raise ValueError("min-minute-fraction must be 0..1")
    return dict(start=d0.isoformat(), end=d1.isoformat(), factors=names,
                horizon=horizon, holdout_start=holdout_start, weighting=weighting,
                neutralize_industry=neutralize_industry,
                industry_csv=str(industry_csv) if industry_csv is not None else None,
                min_group_size=min_group_size, winsor_tail=winsor_tail,
                redundancy_threshold=redundancy_threshold, min_symbols=min_symbols,
                min_dates=min_dates, min_bars=min_bars,
                min_minute_fraction=min_minute_fraction, source=source, session=session)


def _map_rows(file: Path) -> tuple[list[tuple[str, str, str, str]], str]:
    # Historical classifications are USER-SUPPLIED and unverified. Refuse
    # ambiguous overlapping effective dates; never invent missing industries.
    path = factor_db.private_path(file, exists=True)
    if path.suffix.lower() != ".csv":
        raise ValueError("Industry input must be CSV under gitignored local_data")
    mapping: dict[str, list[tuple[date, date]]] = {}
    out = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not {
            "symbol", "industry", "start_date", "end_date"
        }.issubset(reader.fieldnames):
            raise ValueError("Industry CSV requires symbol,industry,start_date,end_date")
        for i, row in enumerate(reader, 2):
            sym = (row["symbol"] or "").strip().upper()
            industry = (row["industry"] or "").strip()
            begin = factor_eval._day((row["start_date"] or "").strip())
            finish = factor_eval._day((row["end_date"] or "").strip()) if (
                row["end_date"] or "").strip() else date(9999, 12, 31)
            if not SYMBOL.fullmatch(sym) or not industry or len(industry) > 80 or begin > finish:
                raise ValueError(f"Invalid dated industry mapping on CSV row {i}")
            periods = mapping.setdefault(sym, [])
            if any(begin <= b and a <= finish for a, b in periods):
                raise ValueError(f"Overlapping industry mappings for {sym} on row {i}")
            periods.append((begin, finish))
            out.append((sym, industry, begin.isoformat(), finish.isoformat()))
            if len(out) > 1_000_000:
                raise ValueError("Too many industry mapping rows")
    if not out:
        raise ValueError("Empty industry mapping")
    return out, hashlib.sha256(path.read_bytes()).hexdigest()


def _base(con, config: dict, industry_rows: list[tuple[str, str, str, str]] | None) -> dict:
    if industry_rows is not None:
        con.execute("CREATE TEMP TABLE v3_industry (symbol VARCHAR,industry VARCHAR,"
                    "start_date DATE,end_date DATE)")
        con.executemany("INSERT INTO v3_industry VALUES (?,?,?,?)", industry_rows)
        join = ("LEFT JOIN v3_industry i ON r.symbol=i.symbol "
                "AND r.trade_date BETWEEN i.start_date AND i.end_date")
        ind = "i.industry"
    else:
        join, ind = "", "NULL::VARCHAR"
    check = factor_eval._eligible("r", config)
    con.execute(f"""CREATE TEMP TABLE v3_base AS
        SELECT r.*, {ind} AS industry FROM v3_raw r {join}
        WHERE {check}""")
    eligible, unmapped = con.execute("""SELECT count(*),
        count(*) FILTER (WHERE industry IS NULL AND trade_date BETWEEN ? AND ?)
        FROM v3_base""", [config["start"], config["end"]]).fetchone()
    if industry_rows is not None and unmapped:
        raise ValueError(f"Industry map missing {unmapped} eligible security/date observations; "
                         "refuse partial sector neutralization")
    raw_count = con.execute("SELECT count(*) FROM v3_raw WHERE trade_date BETWEEN ? AND ?",
                            [config["start"], config["end"]]).fetchone()[0]
    eligible_signal = con.execute(
        "SELECT count(*) FROM v3_base WHERE trade_date BETWEEN ? AND ?",
        [config["start"], config["end"]]).fetchone()[0]
    return {"source_stock_day_rows": raw_count,
            "quality_eligible_stock_day_rows": eligible_signal,
            "quality_excluded_stock_day_rows": raw_count - eligible_signal,
            "industry_unmapped_eligible_rows": unmapped if industry_rows is not None else None}


def _normalize(con, config: dict) -> list[dict]:
    con.execute("CREATE TEMP TABLE v3_norm AS SELECT * FROM v3_base")
    counts = []
    for idx, name in enumerate(config["factors"]):
        # Names come solely from the static V1 factor allowlist.
        stage = f"v3_stage_{idx}"
        tail = config["winsor_tail"]
        min_group = config["min_group_size"]
        group = (
            f"CASE WHEN group_size >= {min_group} "
            "THEN clipped - industry_mean END"
            if config["neutralize_industry"] else "clipped"
        )
        con.execute(f"""
        CREATE TEMP TABLE {stage} AS
        WITH src AS (
            SELECT symbol,trade_date,industry,{name}::DOUBLE AS val
            FROM v3_base WHERE {name} IS NOT NULL AND isfinite({name})
        ), bounds AS (
            SELECT trade_date, quantile_cont(val, {tail}) AS lo,
                   quantile_cont(val, {1-tail}) AS hi
            FROM src GROUP BY trade_date
        ), clipped AS (
            SELECT src.*, greatest(bounds.lo,least(src.val,bounds.hi)) AS clipped
            FROM src JOIN bounds USING (trade_date)
        ), sectors AS (
            SELECT *,
                count(*) OVER (PARTITION BY trade_date,industry) AS group_size,
                avg(clipped) OVER (PARTITION BY trade_date,industry) AS industry_mean
            FROM clipped
        ), residuals AS (
            SELECT symbol,trade_date,{group} AS residual FROM sectors
        ), scaled AS (
            SELECT *,
              count(residual) OVER (PARTITION BY trade_date) AS n,
              avg(residual) OVER (PARTITION BY trade_date) AS mu,
              stddev_samp(residual) OVER (PARTITION BY trade_date) AS sd
            FROM residuals
        )
        SELECT symbol,trade_date,
          CASE WHEN n >= {config['min_symbols']} AND sd > 0 AND isfinite(sd)
               THEN (residual-mu)/sd END AS z
        FROM scaled
        """)
        col = f"z_{name}"
        con.execute(f"ALTER TABLE v3_norm ADD COLUMN {col} DOUBLE")
        con.execute(f"""UPDATE v3_norm AS n SET {col}=s.z FROM {stage} AS s
            WHERE n.symbol=s.symbol AND n.trade_date=s.trade_date""")
        con.execute(f"DROP TABLE {stage}")
        raw, norm, active_dates, pre_holdout_dates = con.execute(
            f"""SELECT count({name}), count({col}),
                count(DISTINCT trade_date) FILTER (WHERE {col} IS NOT NULL),
                count(DISTINCT trade_date) FILTER (
                    WHERE {col} IS NOT NULL AND trade_date < ?)
                FROM v3_norm WHERE trade_date BETWEEN ? AND ?""",
            [config["holdout_start"] or "9999-12-31",
             config["start"], config["end"]]).fetchone()
        counts.append({"factor": name, "raw_non_null_rows": int(raw),
                       "standardized_rows": int(norm),
                       "standardized_dates": int(active_dates),
                       "pre_holdout_standardized_dates": int(pre_holdout_dates),
                       "constant_or_small_cross_section_is_null": True})
    return counts


def _daily_corr(con, a: str, b: str, c: dict) -> list[dict]:
    # Tied midranks; equal-weight daily mean of abs(Spearman), not pooled
    # correlation (which would overweight dates with many listed symbols).
    result = con.execute(f"""
    WITH paired AS (
      SELECT trade_date,z_{a} AS a,z_{b} AS b FROM v3_norm
      WHERE trade_date >= ? AND trade_date < ?
        AND z_{a} IS NOT NULL AND z_{b} IS NOT NULL
    ), ranks AS (
      SELECT trade_date,
        rank() OVER (PARTITION BY trade_date ORDER BY a)
          + (count(*) OVER (PARTITION BY trade_date,a)-1)/2.0 AS ra,
        rank() OVER (PARTITION BY trade_date ORDER BY b)
          + (count(*) OVER (PARTITION BY trade_date,b)-1)/2.0 AS rb
      FROM paired
    )
    SELECT trade_date,count(*) AS n,corr(ra,rb) AS rho FROM ranks
    GROUP BY trade_date HAVING count(*) >= {c['min_symbols']}
    ORDER BY trade_date
    """, [c["start"], c["holdout_start"] or "9999-12-31"])
    return [{"date": str(day), "n": int(n), "rho": factor_eval._as_number(rho)}
            for day, n, rho in result.fetchall()]


def _daily_ic(con, field: str, horizon: int, table: str, c: dict) -> list[dict]:
    # Field and table are generated only from validated factor names or a fixed
    # composite_score field, never directly from CLI SQL text.
    if table not in ("v3_norm", "v3_combo"):
        raise ValueError("Internal invalid evaluation table")
    if horizon not in factor_eval.HORIZONS:
        raise ValueError("Invalid evaluation horizon")
    if field != "composite_score" and field not in {f"z_{f}" for f in c["factors"]}:
        raise ValueError("Invalid evaluation field")
    # Dates come from the complete factor-lake price calendar (not from the
    # subset of stocks surviving quality filters), preventing next-session drift.
    result = con.execute(f"""
    WITH days0 AS (
      SELECT trade_date,lag(trade_date) OVER (ORDER BY trade_date) AS prior
      FROM (SELECT DISTINCT trade_date FROM v3_raw)
    ), calendar AS (
      SELECT trade_date,
        row_number() OVER (ORDER BY trade_date)-1 AS seq,
        lead(trade_date,1) OVER (ORDER BY trade_date) AS entry_day,
        lead(trade_date,{horizon+1}) OVER (ORDER BY trade_date) AS exit_day,
        sum(CASE WHEN prior IS NOT NULL
                    AND date_diff('day',prior,trade_date)>7 THEN 1 ELSE 0 END)
          OVER (ORDER BY trade_date ROWS BETWEEN 1 FOLLOWING AND
                {horizon+1} FOLLOWING) AS future_gaps
      FROM days0
    ), labeled AS (
      SELECT s.symbol,s.trade_date,d.exit_day AS label_exit,
        s.{field}::DOUBLE AS val, ex.close/en.close-1 AS forward_ret
      FROM {table} s
      JOIN calendar d ON s.trade_date=d.trade_date
      JOIN v3_base en ON en.symbol=s.symbol AND en.trade_date=d.entry_day
      JOIN v3_base ex ON ex.symbol=s.symbol AND ex.trade_date=d.exit_day
      WHERE s.trade_date BETWEEN ? AND ?
        AND d.seq % {horizon}=0
        AND d.entry_day IS NOT NULL AND d.exit_day IS NOT NULL
        AND d.future_gaps=0
        AND s.{field} IS NOT NULL AND isfinite(s.{field})
        AND en.close>0 AND ex.close>0
    ), ranks AS (
      SELECT *,
        rank() OVER (PARTITION BY trade_date ORDER BY val)
          + (count(*) OVER (PARTITION BY trade_date,val)-1)/2.0 AS rx,
        rank() OVER (PARTITION BY trade_date ORDER BY forward_ret)
          + (count(*) OVER (PARTITION BY trade_date,forward_ret)-1)/2.0 AS ry
      FROM labeled WHERE isfinite(forward_ret)
    )
    SELECT trade_date,max(label_exit) AS label_exit,
      count(*) AS n,corr(rx,ry) AS rank_ic
    FROM ranks GROUP BY trade_date
    HAVING count(*) >= {c['min_symbols']}
    ORDER BY trade_date
    """, [c["start"], c["end"]])
    return [{"date": str(day), "label_exit": str(exit_day),
             "n": int(n), "rank_ic": factor_eval._as_number(ic)}
            for day, exit_day, n, ic in result.fetchall()]


def _ic_stats(dated: list[dict], min_dates: int) -> dict:
    valid = [x for x in dated if x["rank_ic"] is not None]
    grouped: dict[str, list[float]] = {}
    for row in valid:
        grouped.setdefault(row["date"][:7], []).append(row["rank_ic"])
    months = [{"month": key, "dates": len(vals), "mean_rank_ic": fmean(vals)}
              for key, vals in sorted(grouped.items())]
    if len(valid) < min_dates:
        return {"state": "INSUFFICIENT_DATED_IC", "dates": len(valid),
                "rank_ic_mean": None, "icir_unannualized": None,
                "positive_date_fraction": None, "direction_consistency": None,
                "rolling_20_date_ic_mean_latest": None, "monthly": months}
    vals = [x["rank_ic"] for x in valid]
    mu = fmean(vals)
    sd = stdev(vals) if len(vals) >= 2 else None
    return {
        "state": "EXPLORATORY_UNVERIFIED", "dates": len(vals),
        "rank_ic_mean": mu, "icir_unannualized": mu / sd if sd and sd > 0 else None,
        "positive_date_fraction": sum(v > 0 for v in vals)/len(vals),
        "direction_consistency": sum(v*mu > 0 for v in vals)/len(vals) if mu != 0 else None,
        "rolling_20_date_ic_mean_latest": fmean(vals[-20:]) if len(vals)>=20 else None,
        "monthly": months,
    }


def _periods(daily: list[dict], c: dict) -> dict:
    holdout = c["holdout_start"]
    full = _ic_stats(daily, c["min_dates"])
    if holdout is None:
        return {"all_descriptive": full, "train_matured": None, "holdout": None}
    # IMPORTANT: training labels must have fully exited *before* holdout.
    train = [d for d in daily if d["date"] < holdout and d["label_exit"] < holdout]
    held = [d for d in daily if d["date"] >= holdout]
    embargoed = sum(1 for d in daily
                    if d["date"] < holdout <= d["label_exit"])
    return {"all_descriptive": full,
            "train_matured": _ic_stats(train, c["min_dates"]),
            "holdout": _ic_stats(held, c["min_dates"]),
            "train_observations_purged_due_to_label_overlap": embargoed}


def _correlations(con, c: dict) -> list[dict]:
    result = []
    names = c["factors"]
    for i, a in enumerate(names):
        for b in names[i+1:]:
            dated = _daily_corr(con, a, b, c)
            vals = [d["rho"] for d in dated if d["rho"] is not None]
            sufficient = len(vals) >= c["min_dates"]
            result.append({
                "a": a, "b": b, "dates": len(vals),
                "mean_signed_spearman": fmean(vals) if sufficient else None,
                "mean_absolute_spearman": fmean(abs(v) for v in vals) if sufficient else None,
                "state": "DESCRIPTIVE_CORRELATION" if sufficient else "INSUFFICIENT_DATES",
            })
    return result


def _select(c: dict, diagnostics: list[dict], corr: list[dict],
            coverage: list[dict]) -> dict:
    # No test/holdout labels can determine factor redundancy selection.
    active = {r["factor"]: (r["pre_holdout_standardized_dates"]
                            if c["holdout_start"] else r["standardized_dates"])
              for r in coverage}
    unusable = [n for n in c["factors"] if active[n] < c["min_dates"]]
    train = {d["factor"]: d["ic_stability"]["train_matured"] for d in diagnostics}
    means = {n: train[n]["rank_ic_mean"] if train[n] is not None else None
             for n in c["factors"]}
    order = {n: i for i, n in enumerate(c["factors"])}
    if c["holdout_start"] is not None:
        ranked = sorted(c["factors"], key=lambda n: (
            -(abs(means[n]) if means[n] is not None else -1), order[n]))
    else:
        ranked = c["factors"]
    edges = {(row["a"], row["b"]): row["mean_absolute_spearman"] for row in corr}
    selected, dropped, untested = [], [], []
    for n in ranked:
        if n in unusable:
            continue
        conflict = None
        for earlier in selected:
            strength = edges.get((n, earlier), edges.get((earlier, n)))
            if strength is None:
                untested.append([n, earlier])
            elif strength >= c["redundancy_threshold"]:
                conflict = {"factor": n, "retained": earlier, "abs_spearman": strength}
                break
        if conflict:
            dropped.append(conflict)
        else:
            selected.append(n)
    return {"selected_factors": selected, "redundant_factors": dropped,
            "excluded_insufficient_standardized_dates": unusable,
            "pairs_insufficient_for_redundancy_decision": untested,
            "selection_order": ranked,
            "selection_information": (
                "Training-only matured IC priority and training-only factor correlations"
                if c["holdout_start"] else
                "User factor order and full-period unsupervised correlations; no learned IC priorities"),
            "not_proof_of_independence": True}


def _weights(c: dict, selected: list[str], diagnostics: list[dict]) -> tuple[dict | None, str]:
    if not selected:
        return None, "NO_SELECTED_FACTORS"
    if c["weighting"] == "equal":
        return {name: 1.0 / len(selected) for name in selected}, "FIXED_EQUAL_POSITIVE"
    lookup = {row["factor"]: row["ic_stability"]["train_matured"] for row in diagnostics}
    ic = {}
    for name in selected:
        rec = lookup[name]
        if rec is None or rec["state"] != "EXPLORATORY_UNVERIFIED":
            return None, "INSUFFICIENT_MATURED_TRAINING_IC"
        ic[name] = rec["rank_ic_mean"]
    total = sum(abs(v) for v in ic.values())
    if not total or not math.isfinite(total):
        return None, "ZERO_TRAINING_IC_MAGNITUDE"
    # Signed weights reverse negatively correlated factors, learned only
    # from labels matured before the holdout starts.
    return {k: v / total for k, v in ic.items()}, "MATURED_TRAINING_IC_SIGNED"


def _score(con, weights: dict[str, float], c: dict) -> tuple[dict, list[dict]]:
    # Missing factor inputs NEVER silently reweight by survivors.
    terms = [f"({weight:.17g}) * z_{name}" for name, weight in weights.items()]
    condition = " AND ".join(f"z_{n} IS NOT NULL" for n in weights)
    denom = sum(abs(w) for w in weights.values())
    formula = "(" + " + ".join(terms) + f") / ({denom:.17g})"
    con.execute(f"""CREATE TEMP TABLE v3_combo AS
         SELECT symbol,trade_date,({formula}) AS composite_score
         FROM v3_norm WHERE {condition}""")
    rows, dates = con.execute("""SELECT count(*),count(DISTINCT trade_date)
       FROM v3_combo WHERE trade_date BETWEEN ? AND ?""",
       [c["start"], c["end"]]).fetchone()
    dated = _daily_ic(con, "composite_score", c["horizon"], "v3_combo", c)
    return {"scored_stock_day_rows": int(rows), "scored_dates": int(dates),
            "ic_stability": _periods(dated, c),
            "only_complete_case": True,
            "no_execution_or_portfolio_pnl": True}, dated


def analyze(*, start: str, end: str, source: str = "hf", session: str = "rth",
            factors: list[str] | None = None, horizon: int = 1,
            holdout_start: str | None = None, weighting: str = "equal",
            neutralize_industry: bool = False, industry_csv: Path | None = None,
            min_group_size: int = 5, winsor_tail: float = 0.01,
            redundancy_threshold: float = 0.85, min_symbols: int = 30,
            min_dates: int = 8, min_bars: int = 60,
            min_minute_fraction: float = 0.85,
            memory: str = "4GB", threads: int = 4,
            exploratory: bool = False, save: bool = True,
            save_scores: bool = False) -> dict:
    c = _validate(start=start, end=end, factors=factors, horizon=horizon,
                  holdout_start=holdout_start, weighting=weighting,
                  neutralize_industry=neutralize_industry, industry_csv=industry_csv,
                  min_group_size=min_group_size, winsor_tail=winsor_tail,
                  redundancy_threshold=redundancy_threshold,
                  min_symbols=min_symbols, min_dates=min_dates, min_bars=min_bars,
                  min_minute_fraction=min_minute_fraction, source=source, session=session)
    v2config = factor_eval._config(
        start=c["start"], end=c["end"], source=source, session=session,
        factors=c["factors"], horizons=[horizon], holdout_start=holdout_start,
        min_symbols=min_symbols, min_dates=min_dates, min_bars=min_bars,
        min_minute_fraction=min_minute_fraction, cost_bps_per_leg_roundtrip=20.0)
    paths, metas = factor_eval._inputs(v2config)
    provenance = factor_db._fingerprint(paths)
    header = {
        "schema": VERSION, "config": c,
        "input_months": [p.stem[-7:] for p in paths],
        "input_fingerprint": provenance,
        "source_price_adjustment": sorted(set(m["source_price_adjustment"] for m in metas)),
        "point_in_time_validated": False, "historical_security_ids_verified": False,
        "industry_classifications_verified": False, "corporate_actions_verified": False,
        "data_rights_verified": False, "approved_for_model_training": False,
        "live_execution_enabled": False, "research_only": True,
    }
    if not exploratory:
        return {**header, "mode": "plan_only", "requires_network": False,
                "industry_neutralization_state": (
                    "REQUIRES_USER_SUPPLIED_DATED_MAPPING" if neutralize_industry
                    else "NOT_APPLIED_NO_DATED_MAPPING"),
                "reason": "Add --exploratory for research computations; no ML/trading approval."}
    industry_rows, industry_hash = _map_rows(industry_csv) if industry_csv else (None, None)
    con = factor_db._db(memory, threads)
    try:
        con.execute(f"CREATE TEMP TABLE v3_raw AS SELECT symbol,trade_date,close,"
                    f"observed_5m_bars,observed_1m_bars,{','.join(factor_db.FEATURES)} "
                    f"FROM {factor_db._files_sql(paths)}")
        invalid = con.execute("""SELECT count(*) FROM v3_raw WHERE symbol IS NULL
            OR trade_date IS NULL OR close IS NULL OR close<=0 OR NOT isfinite(close)""").fetchone()[0]
        if invalid:
            raise ValueError(f"Invalid V1 key/close rows: {invalid}")
        duplicate = con.execute("""SELECT 1 FROM v3_raw GROUP BY symbol,trade_date
                                  HAVING count(*) > 1 LIMIT 1""").fetchone()
        if duplicate:
            raise ValueError("V1 materialized input has duplicate symbol/trade_date")
        funnel = _base(con, c, industry_rows)
        coverage = _normalize(con, c)
        correlations = _correlations(con, c)
        diagnostics = []
        for name in c["factors"]:
            dated = _daily_ic(con, f"z_{name}", horizon, "v3_norm", c)
            diagnostics.append({
                "factor": name, "horizon_sessions": horizon,
                "ic_stability": _periods(dated, c),
                "daily_ic_preview": dated[:10],
            })
        usable_date_counts = {
            row["factor"]: (row["pre_holdout_standardized_dates"]
                            if c["holdout_start"] else row["standardized_dates"])
            for row in coverage
        }
        matrix = {
            name: {
                other: (1.0 if name == other
                        and usable_date_counts[name] >= c["min_dates"] else None)
                for other in c["factors"]
            }
            for name in c["factors"]
        }
        for pair in correlations:
            matrix[pair["a"]][pair["b"]] = pair["mean_signed_spearman"]
            matrix[pair["b"]][pair["a"]] = pair["mean_signed_spearman"]
        selection = _select(c, diagnostics, correlations, coverage)
        weights, weighting_state = _weights(c, selection["selected_factors"], diagnostics)
        composite, composite_dated = (
            _score(con, weights, c) if weights is not None else (None, [])
        )
        result = {
            **header, "mode": "exploratory_descriptive",
            "industry_input_sha256": industry_hash,
            "industry_neutralization_state": (
                "APPLIED_USER_SUPPLIED_UNVERIFIED_EFFECTIVE_DATES"
                if industry_rows is not None else "NOT_APPLIED_NO_DATED_MAPPING"),
            "standardization": "daily winsorized cross-sectional z-score; "
                "industry demean before global z if dated map explicitly supplied; "
                "constant or underpopulated dates return NULL",
            "quality_funnel": funnel,
            "factor_coverage": coverage,
            "factor_pair_correlations": correlations,
            "correlation_matrix_signed_spearman": matrix,
            "redundancy_selection": selection,
            "factor_diagnostics": diagnostics,
            "multi_factor": {
                "weighting_state": weighting_state,
                "factor_weights": weights,
                "composite": composite,
                "daily_ic_preview": composite_dated[:10],
                "not_an_investment_signal": True,
            },
            "time_alignment": "Signal after T close; hypothetical next-session "
                "close entry, (T+h+1) close exit; no real execution fills",
            "holdout_safety": "IC-learned weights and redundancy priorities use "
                "ONLY mature pre-holdout labels; pre-holdout labels overlapping "
                "holdout are purged; no external PIT universe certification.",
            "score_interpretation": "Per-stock z-scores describe factor exposures, "
                "NOT predicted returns or recommendations.",
        }
        if save or save_scores:
            digest = hashlib.sha256(
                (json.dumps(c, sort_keys=True) + provenance + (industry_hash or "")).encode()
            ).hexdigest()[:12]
            output = factor_db.private_path(
                ROOT / "reports" / f"v3_{c['start']}_{c['end']}_{digest}.json")
            output.parent.mkdir(parents=True, exist_ok=True)
            if save:
                tmp = factor_db.private_path(output.with_suffix(".json.part"))
                try:
                    tmp.write_text(json.dumps(result, indent=2, sort_keys=True,
                                              allow_nan=False) + "\n", encoding="utf-8")
                    os.replace(tmp, output)
                finally:
                    tmp.unlink(missing_ok=True)
                result["report_path"] = str(output)
            if save_scores and weights is not None:
                target = factor_db.private_path(
                    ROOT / "scores" / f"scores_{c['start']}_{c['end']}_{digest}.parquet")
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = factor_db.private_path(target.with_suffix(".parquet.part"))
                try:
                    # Only research-only local output, no broker/signal handoff.
                    con.execute(f"""COPY (
                      SELECT symbol,trade_date,composite_score,
                          '{source}'::VARCHAR AS source,
                          '{session}'::VARCHAR AS session,
                          '{VERSION}'::VARCHAR AS schema_version,
                          TRUE::BOOLEAN AS research_only,
                          FALSE::BOOLEAN AS approved_for_model_training,
                          FALSE::BOOLEAN AS point_in_time_validated,
                          FALSE::BOOLEAN AS live_execution_enabled
                      FROM v3_combo
                      WHERE trade_date BETWEEN DATE '{c['start']}' AND DATE '{c['end']}'
                      ORDER BY trade_date,symbol
                    ) TO {factor_db._quoted(tmp)} (FORMAT PARQUET,COMPRESSION ZSTD)""")
                    os.replace(tmp, target)
                finally:
                    tmp.unlink(missing_ok=True)
                result["research_only_scores_path"] = str(target)
        return result
    finally:
        con.close()
