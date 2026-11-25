"""V6.1 audit actual research samples and folds without fitting any model."""
from __future__ import annotations

from pathlib import Path

from atlaspm.data import factor_db as factor
from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data import hf_stock5m_session_v4 as v4
from atlaspm.data.market_db import private_path
from atlaspm.research import real_dataset_v6 as dataset

SCHEMA="atlaspm_real_research_preflight_v6.1"


def _prefix(values):
    result=[0]
    for value in values:
        result.append(result[-1]+value)
    return result


def _sum(prefix,start,end):
    return prefix[end+1]-prefix[start]


def _check_partition(cx,path,month,features,horizons):
    src=factor._files_sql([path])
    complete=" AND ".join(f"coalesce(isfinite(d.{f}),false)" for f in features)
    bad_labels=" OR ".join(f"""(
        (d.label_{h}d IS NULL) <> (d.label_end_index_{h}d IS NULL)
        OR (d.label_{h}d IS NOT NULL AND NOT coalesce(
            isfinite(d.label_{h}d) AND d.label_{h}d>=-1
            AND d.label_end_index_{h}d=d.session_index+{h}
            AND d.label_end_index_{h}d<(SELECT count(*) FROM v6_sessions),false)))""" for h in horizons)
    rows,bad=cx.execute(f"""SELECT count(*),count(*) FILTER (WHERE NOT coalesce(
        d.symbol IS NOT NULL AND length(d.symbol) BETWEEN 1 AND 32
        AND strftime(d.trade_date,'%Y-%m')=?
        AND d.session_index=s.session_index AND isfinite(d.close) AND d.close>0
        AND d.last_bar_available_at_ms>s.open_ms AND d.last_bar_available_at_ms<=s.close_ms
        AND d.last_bar_available_at_ms%300000=0
        AND d.earliest_assumed_feature_time_ms=s.close_ms
        AND d.features_complete=({complete}) AND NOT ({bad_labels}),false))
        FROM {src} d LEFT JOIN v6_sessions s ON d.trade_date=s.trade_date""",[month]).fetchone()
    dup=cx.execute(f"SELECT 1 FROM {src} GROUP BY symbol,session_index HAVING count(*)>1 LIMIT 1").fetchone()
    if not rows or bad or dup:
        raise ValueError(f"Invalid sample partition {month}: rows={rows}, invalid={bad}, duplicates={bool(dup)}")
    return int(rows)


def _check_labels(cx,paths,month,horizons):
    """Recompute labels from sample price paths, not from stored label flags."""
    src=factor._files_sql(paths)
    # Count the rows in the exact calendar range, independently of LEAD.
    # A duplicate key is rejected before this query runs.
    expr=[]
    for h in horizons:
        expr.extend([f"count(*) OVER (PARTITION BY symbol ORDER BY session_index RANGE BETWEEN CURRENT ROW AND {h} FOLLOWING) AS n{h}",
                     f"last_value(close) OVER (PARTITION BY symbol ORDER BY session_index RANGE BETWEEN CURRENT ROW AND {h} FOLLOWING) AS p{h}"])
    checks=[]
    for h in horizons:
        expected=f"CASE WHEN n{h}={h+1} AND isfinite(p{h}/close) THEN p{h}/close-1 END"
        checks.append(f"label_{h}d IS DISTINCT FROM ({expected})")
    mismatch=cx.execute(f"""WITH recalculated AS (SELECT *,{", ".join(expr)} FROM {src})
        SELECT count(*) FROM recalculated WHERE strftime(trade_date,'%Y-%m')=?
        AND ({" OR ".join(checks)})""",[month]).fetchone()[0]
    if mismatch:
        raise ValueError(f"Label path reconciliation failed for {month}: {mismatch} rows")


def _source_inputs(manifest):
    paths, missing=dataset._inputs(manifest["config"]["months"])
    if missing:
        raise ValueError("Dataset factor inputs missing")
    actual={m:{"data":dataset._stamp(p),"meta":dataset._stamp(p.with_suffix(".json"))}
            for m,p in paths.items()}
    if actual!=manifest["inputs"]:
        raise ValueError("Dataset factor input revision mismatch")
    return paths


def _check_feature_source(cx,sample,source,features):
    # EXCEPT ALL detects changed values, omitted rows and duplicated observations.
    cols=", ".join(features)
    a=f"SELECT symbol,trade_date,close,last_bar_available_at_ms,{cols} FROM {factor._files_sql([sample])}"
    b=f"SELECT symbol,trade_date,close,last_observed_bar_ms+300000 AS last_bar_available_at_ms,{cols} FROM {factor._files_sql([source])}"
    mismatch=cx.execute(f"""SELECT 1 FROM (
        ({a} EXCEPT ALL {b}) UNION ALL ({b} EXCEPT ALL {a})
        ) LIMIT 1""").fetchone()
    if mismatch:
        raise ValueError("Dataset feature/source reconciliation failed")


def run(dataset_id,*,minimum_train_rows=1000,minimum_test_rows=100,memory="2GB",threads=2,require_lineage=False):
    for n in (minimum_train_rows,minimum_test_rows):
        if type(n) is not int or n<1:
            raise ValueError("Minimum sample counts must be positive integers")
    m=dataset.inspect(dataset_id)
    if require_lineage and not m.get("lineage_id"):
        raise ValueError("Dataset lacks unified factor lineage; rebuild through canonical bridge")
    c=m["config"]
    months,config=dataset._config(c["months"][0],c["months"][-1],c["features"],c["horizons"],
        c["minimum_train_sessions"],c["test_sessions"],c["embargo_sessions"])
    if config!=c:
        raise ValueError("Dataset configuration is not canonical")
    calendar=dataset._calendar(months)
    if dataset._digest(calendar)!=m["calendar_sha256"]:
        raise ValueError("Calendar has changed; rebuild dataset")
    source_paths=_source_inputs(m)
    if m.get("lineage_id") is not None:
        from atlaspm.data.unified_pipeline import factor_preflight
        current=factor_preflight(months[0],months[-1],memory=memory,threads=threads)
        if not current["structural_preflight_passed"] or current["evidence_id"]!=m["lineage_id"]:
            raise ValueError("Unified factor lineage revision mismatch")
    # Do not trust a user-edited manifest's fold or sample totals.
    paths={p["month"]:private_path(Path(p["path"]),exists=True) for p in m["partitions"]}
    cx=factor._db(memory,threads)
    horizon_counts={h:[0]*len(calendar) for h in c["horizons"]}
    pred=[0]*len(calendar)
    observed=[0]*len(calendar)
    try:
        dataset._install_calendar(cx,calendar)
        for month,p in paths.items():
            _check_partition(cx,p,month,c["features"],c["horizons"])
            _check_feature_source(cx,p,source_paths[month],c["features"])
        for month,p in paths.items():
            window=dataset._window(month,months,calendar,max(c["horizons"]))
            _check_labels(cx,[paths[k] for k in window],month,c["horizons"])
            src=factor._files_sql([p])
            cols=", ".join(f"count(*) FILTER (WHERE features_complete AND label_{h}d IS NOT NULL)" for h in c["horizons"])
            for row in cx.execute(f"""SELECT session_index,count(*),count(*) FILTER (WHERE features_complete),{cols}
                         FROM {src} GROUP BY session_index""").fetchall():
                i,n,pred_n,*counts=row
                observed[i]=int(n)
                pred[i]=int(pred_n)
                for h,count in zip(c["horizons"],counts):
                    horizon_counts[h][i]=int(count)
    finally:
        cx.close()
    if dataset.inspect(dataset_id)!=m:
        raise ValueError("Dataset manifest changed during preflight")
    _source_inputs(m)  # source factors must still match the recorded revision
    if m.get("lineage_id") is not None:
        current=factor_preflight(months[0],months[-1],memory=memory,threads=threads)
        if not current["structural_preflight_passed"] or current["evidence_id"]!=m["lineage_id"]:
            raise ValueError("Unified factor lineage changed during preflight")
    prediction_prefix=_prefix(pred)
    folds={}
    totals={}
    for h,counts in horizon_counts.items():
        prefix=_prefix(counts)
        rows=[]
        for f in dataset._folds(calendar,h,c["minimum_train_sessions"],c["test_sessions"],c["embargo_sessions"]):
            train=_sum(prefix,f["train_start_index"],f["train_end_index"])
            test=_sum(prefix,f["test_start_index"],f["test_end_index"])
            predictions=_sum(prediction_prefix,f["test_start_index"],f["test_end_index"])
            rows.append({**f,"train_labeled_rows":train,"test_labeled_rows":test,
                         "test_feature_complete_rows":predictions,"test_missing_labels":predictions-test,
                         "sample_count_pass":train>=minimum_train_rows and test>=minimum_test_rows})
        folds[str(h)]=rows
        totals[str(h)]={"labeled_feature_complete_rows":sum(counts),"fold_count":len(rows),
                        "folds_meeting_sample_minimum":sum(f["sample_count_pass"] for f in rows)}
    report={"schema":SCHEMA,**v4.GUARDS,"dataset_id":dataset_id,
            "status":"STRUCTURAL_PREFLIGHT_COMPLETE_TRAINING_BLOCKED",
            "observed_rows":sum(observed),"feature_complete_rows":sum(pred),
            "calendar_sessions":len(calendar),"sessions_without_observations":sum(n==0 for n in observed),
            "minimum_train_rows":minimum_train_rows,"minimum_test_rows":minimum_test_rows,
            "label_paths_reconciled":True,"feature_source_reconciled":True,
            "lineage_id":m.get("lineage_id"),"approved_for_backtest":False,
            "point_in_time_verified":False,"broker_order_submission_enabled":False,
            "model_registry_admission":False,"horizons":totals,"folds":folds,
            "blockers":m["blockers"],"models_fitted":0,
            "warning":"Fold counts are structural only. Unverified security identity, actions, rights and feature timing prevent formal training."}
    path=dataset.BASE/dataset_id[:24]/"preflight.json"
    report["report_path"]=str(private_path(path))
    ev._json(path,report)
    return report
