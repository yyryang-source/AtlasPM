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


def _install_identity_review(cx,calendar,as_of):
    """Join existing reviewed claims, never infer identities from a ticker.

    Each session requires a prior-known resolved identity, agreeing with the
    retrospective ledger at an explicit later review cutoff. Known actions
    are exclusion flags, not guessed split-adjusted/total-return labels.
    Only bounded temporary rows are created, no second persistent warehouse.
    """
    from bisect import bisect_left
    from collections import defaultdict
    from datetime import date, datetime, timezone
    from atlaspm.data import quality_review, unified

    if (not isinstance(as_of,str) or date.fromisoformat(as_of).isoformat()!=as_of
            or not calendar or not calendar[-1][0]<as_of<=datetime.now(timezone.utc).date().isoformat()
            or len(calendar)>550):
        raise ValueError("IDENTITY_REVIEW_REQUIRES_LATER_CUTOFF_AND_BOUNDED_CALENDAR")
    before=quality_review._ledger_digest()
    retro=unified.review_security(as_of=as_of,trade_date=calendar[-1][0])
    intervals=defaultdict(list)
    for row in retro["identities"]:
        intervals[row["symbol"]].append(row)
    dates=[d for d,_,_ in calendar]
    actions=set()
    for row in retro["actions"]:
        effective=row["effective_date"]
        if dates[0]<=effective<=dates[-1]:
            # Weekend/holiday actions belong to the following session.
            actions.add((bisect_left(dates,effective),row["security_id"]))
    cx.execute("""CREATE OR REPLACE TEMP TABLE v6_identity_review(
        session_index INTEGER, symbol VARCHAR, security_id VARCHAR,
        action_on_session BOOLEAN)""")
    unresolved=0
    mapped=0
    disagreed=0
    snapshots=[]
    for i,day in enumerate(dates):
        snap=unified.review_security(as_of=day,trade_date=day)
        unresolved+=len(snap["unresolved"])
        if len(snap["members"])>50000:
            raise ValueError("IDENTITY_REVIEW_MEMBER_BUDGET")
        symbols=set()
        batch=[]
        for row in snap["members"]:
            symbol,sid=row["symbol"],row["security_id"]
            if symbol in symbols:
                raise ValueError("IDENTITY_REVIEW_AMBIGUOUS_MEMBER")
            symbols.add(symbol)
            matches=[x for x in intervals[symbol] if x["valid_from"]<=day
                     and (x["valid_to"] is None or day<x["valid_to"])]
            if len(matches)!=1 or matches[0]["security_id"]!=sid:
                disagreed+=1
                continue
            batch.append((i,symbol,sid,(i,sid) in actions))
        mapped+=len(batch)
        if mapped>10_000_000:
            raise ValueError("IDENTITY_REVIEW_TOTAL_MEMBER_BUDGET")
        # Vectorized insert, no individual database round-trip per member.
        for start in range(0,len(batch),2048):
            columns=list(zip(*batch[start:start+2048]))
            cx.execute("""INSERT INTO v6_identity_review
                SELECT unnest(?::INTEGER[]),unnest(?::VARCHAR[]),
                       unnest(?::VARCHAR[]),unnest(?::BOOLEAN[])""",
                       [list(x) for x in columns])
        snapshots.append([day,quality_review._digest(snap)])
    if (quality_review._ledger_digest()!=before or
            quality_review._digest(unified.review_security(as_of=as_of,trade_date=dates[-1]))
                !=quality_review._digest(retro)):
        raise ValueError("IDENTITY_REVIEW_EVIDENCE_CHANGED")
    return {"as_of":as_of,"ledger_claims_sha256":before,
            "retrospective_snapshot_sha256":quality_review._digest(retro),
            "daily_snapshots_sha256":quality_review._digest(snapshots),
            "prior_known_agreeing_member_days":mapped,"retrospective_disagreements":disagreed,
            "daily_unresolved_claim_observations":unresolved,
            "retrospective_unresolved_claims":len(retro["unresolved"]),
            "visible_action_security_sessions":len(actions),
            "source_independence_verified":False,"action_completeness_verified":False,
            "feature_identity_history_verified":False,"adjustment_applied":False}


# Earliest close/volume observation used by each existing factor. Volatility
# of 21 one-session returns needs 22 closes, including the signal session.
FEATURE_LOOKBACK = {"ret_1d":1,"mom_5d":5,"mom_21d":21,"mom_63d":63,
                    "mom_126d":126,"mom_252d":252,"volatility_21d":21,
                    "price_vs_sma_20d":19,"volume_ratio_20d":20,
                    "avg_dollar_volume_20d":19,"intraday_return":0,"intraday_range":0}


def _feature_history_condition(lookback):
    return (f"hist_close_n={lookback+1} AND hist_id_n={lookback+1}"
            " AND hist_id_min=hist_id_max AND hist_action_n=0")


def _identity_path_condition(h):
    return (f"features_complete AND label_{h}d IS NOT NULL AND close_n{h}={h+1}"
            f" AND id_n{h}={h+1} AND id_min{h}=id_max{h}")


def _check_labels(cx,paths,month,horizons,*,identity_review=False,matrix=None,feature_lookback=None):
    """Recompute observed-price labels and count stricter closing-bar paths.

    A last observed intraday bar is not necessarily the session close.
    Reuse the existing window pass to count complete closing-bar paths
    (including every intermediate session), without changing stored labels.
    A closing bar is still not proof of vendor completeness or availability.
    """
    src=factor._files_sql(paths)
    if identity_review:
        src=f"""(SELECT d.*,i.security_id AS review_security_id,
                coalesce(i.action_on_session,false) AS review_action_on_session
                FROM {src} d LEFT JOIN v6_identity_review i
                ON d.session_index=i.session_index AND d.symbol=i.symbol)"""
    # Count the rows in the exact calendar range, independently of LEAD.
    # A duplicate key is rejected before this query runs.
    expr=[]
    for h in horizons:
        expr.extend([f"count(*) OVER (PARTITION BY symbol ORDER BY session_index RANGE BETWEEN CURRENT ROW AND {h} FOLLOWING) AS n{h}",
                     f"last_value(close) OVER (PARTITION BY symbol ORDER BY session_index RANGE BETWEEN CURRENT ROW AND {h} FOLLOWING) AS p{h}",
                     f"count(*) FILTER (WHERE last_bar_available_at_ms=earliest_assumed_feature_time_ms) OVER (PARTITION BY symbol ORDER BY session_index RANGE BETWEEN CURRENT ROW AND {h} FOLLOWING) AS close_n{h}"])
        if identity_review:
            w=f"OVER (PARTITION BY symbol ORDER BY session_index RANGE BETWEEN CURRENT ROW AND {h} FOLLOWING)"
            expr.extend([f"count(review_security_id) {w} AS id_n{h}",
                         f"min(review_security_id) {w} AS id_min{h}",
                         f"max(review_security_id) {w} AS id_max{h}",
                         f"count(*) FILTER (WHERE review_action_on_session) {w} AS act_n{h}"])
    if feature_lookback is not None:
        if not identity_review or type(feature_lookback) is not int or not 0<=feature_lookback<=252:
            raise ValueError("INVALID_FEATURE_HISTORY_REVIEW")
        w=f"OVER (PARTITION BY symbol ORDER BY session_index RANGE BETWEEN {feature_lookback} PRECEDING AND CURRENT ROW)"
        expr.extend([f"count(*) FILTER (WHERE last_bar_available_at_ms=earliest_assumed_feature_time_ms) {w} AS hist_close_n",
                     f"count(review_security_id) {w} AS hist_id_n",
                     f"min(review_security_id) {w} AS hist_id_min",
                     f"max(review_security_id) {w} AS hist_id_max",
                     f"count(*) FILTER (WHERE review_action_on_session) {w} AS hist_action_n"])
    checks=[]
    for h in horizons:
        expected=f"CASE WHEN n{h}={h+1} AND isfinite(p{h}/close) THEN p{h}/close-1 END"
        checks.append(f"label_{h}d IS DISTINCT FROM ({expected})")
    # Only aggregate integer counts per session in Python. The independent
    # partition check already verified assumed feature time against calendar.
    strict=", ".join(f"count(*) FILTER (WHERE features_complete AND label_{h}d IS NOT NULL AND close_n{h}={h+1})" for h in horizons)
    if identity_review:
        for h in horizons:
            condition=_identity_path_condition(h)
            strict+=f",count(*) FILTER (WHERE {condition}),count(*) FILTER (WHERE {condition} AND act_n{h}=0)"
        if feature_lookback is not None:
            for h in horizons:
                strict+=f",count(*) FILTER (WHERE {_identity_path_condition(h)} AND act_n{h}=0 AND {_feature_history_condition(feature_lookback)})"
    counts=cx.execute(f"""WITH recalculated AS (SELECT *,{", ".join(expr)} FROM {src})
        SELECT session_index,count(*) FILTER (WHERE {" OR ".join(checks)}),
               count(*) FILTER (WHERE last_bar_available_at_ms=earliest_assumed_feature_time_ms),{strict}
        FROM recalculated WHERE strftime(trade_date,'%Y-%m')=?
        GROUP BY session_index ORDER BY session_index""",[month]).fetchall()
    mismatch=sum(row[1] for row in counts)
    if mismatch:
        raise ValueError(f"Label path reconciliation failed for {month}: {mismatch} rows")
    if matrix is not None:
        # The same retrospective eligibility predicate as the counts above.
        # Partition filtering is AFTER window evaluation, including when
        # label maturity falls in the next month. No labels in feature columns.
        h=matrix["horizon"]
        f=matrix["fold"]
        if not identity_review or h not in horizons:
            raise ValueError("FOLD_MATRIX_IDENTITY_REVIEW_REQUIRED")
        lo,hi,tl,th=(f[k] for k in ("train_start_index","train_end_index","test_start_index","test_end_index"))
        if any(lo<=i<=hi or tl<=i<=th for i,*_ in counts):
            cols=", ".join(matrix["features"])
            history_filter=""
            if matrix.get("require_feature_history"):
                if feature_lookback is None:
                    raise ValueError("FOLD_MATRIX_FEATURE_HISTORY_REQUIRED")
                history_filter=" AND "+_feature_history_condition(feature_lookback)
            cx.execute(f"""INSERT INTO v6_fold_matrix
                WITH recalculated AS (SELECT *,{", ".join(expr)} FROM {src})
                SELECT CASE WHEN session_index BETWEEN ? AND ? THEN 'train' ELSE 'test' END,
                       session_index,review_security_id,{cols},label_{h}d,label_end_index_{h}d
                FROM recalculated WHERE strftime(trade_date,'%Y-%m')=?
                    AND ((session_index BETWEEN ? AND ?) OR (session_index BETWEEN ? AND ?))
                    AND {_identity_path_condition(h)} AND act_n{h}=0{history_filter}""",
                [lo,hi,month,lo,hi,tl,th])
    return [(int(i),int(close_count),*[int(n) for n in strict_counts])
            for i,_,close_count,*strict_counts in counts]


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


def _matrix_request(cx,calendar,config,request):
    """Select one canonical purged fold, never caller-supplied date boundaries."""
    if (not isinstance(request,dict) or not {"horizon","fold_index","array_budget_mib"}<=set(request)
            or set(request)-{"horizon","fold_index","array_budget_mib","require_feature_history"}
            or type(request.get("require_feature_history",False)) is not bool
            or type(request["horizon"]) is not int or request["horizon"] not in config["horizons"]
            or type(request["fold_index"]) is not int or request["fold_index"]<0
            or type(request["array_budget_mib"]) is not int or not 1<=request["array_budget_mib"]<=256):
        raise ValueError("INVALID_FOLD_MATRIX_REQUEST")
    h=request["horizon"]
    folds=dataset._folds(calendar,h,config["minimum_train_sessions"],config["test_sessions"],
                         config["embargo_sessions"])
    if request["fold_index"]>=len(folds):
        raise ValueError("FOLD_MATRIX_INDEX_OUT_OF_RANGE")
    f=folds[request["fold_index"]]
    if (f["latest_training_label_end_index"]>=f["test_start_index"]-config["embargo_sessions"]
            or f["test_end_index"]+h>=len(calendar)):
        raise ValueError("FOLD_MATRIX_TEMPORAL_ISOLATION")
    cols=", ".join(f"{name} DOUBLE" for name in config["features"])
    cx.execute(f"""CREATE OR REPLACE TEMP TABLE v6_fold_matrix(
        split VARCHAR,session_index INTEGER,security_id VARCHAR,{cols},
        target DOUBLE,label_end_index INTEGER)""")
    return {**request,"fold":f,"features":config["features"]}


def _review_matrix_arrays(cx,matrix,expected_train,expected_test):
    """Exercise the existing scoped array allocator on real selected fold rows.

    Feature and target arrays are separate, never passed to a fitting API.
    Hash rows in deterministic order, not arbitrary memory layout or temp paths.
    Only bounded batches enter Python, and spilled arrays are deleted on exit.
    """
    import hashlib
    import struct
    from math import isfinite
    from atlaspm.research.training_memory import FoldArrays
    from atlaspm.data.market_db import private_path
    import numpy as np

    feature_names=matrix["features"]
    cols=", ".join(feature_names)
    h=matrix["horizon"]
    f=matrix["fold"]
    # Refuse duplicate security/session rows even if two provider symbols
    # unexpectedly mapped to the same security.
    duplicate=cx.execute("""SELECT 1 FROM v6_fold_matrix
        GROUP BY split,session_index,security_id HAVING count(*)>1 LIMIT 1""").fetchone()
    if duplicate:
        raise ValueError("FOLD_MATRIX_DUPLICATE_SECURITY_SESSION")
    sizes=dict(cx.execute("SELECT split,count(*) FROM v6_fold_matrix GROUP BY split").fetchall())
    if set(sizes)-{"train","test"} or sizes.get("train",0)!=expected_train or sizes.get("test",0)!=expected_test:
        raise ValueError("FOLD_MATRIX_COUNTS_MISMATCH")
    root=private_path(Path("local_data/training/v6/matrix_spill"))
    # Do not allow a single fold to fill arbitrary amounts of storage.
    bytes_required=(expected_train+expected_test)*(len(feature_names)+1)*8
    if bytes_required>2*1024**3:
        raise ValueError("FOLD_MATRIX_BYTE_LIMIT")
    result={}
    with FoldArrays(matrix["array_budget_mib"],root) as arrays:
        for split,n,lo,hi in (("train",expected_train,f["train_start_index"],f["train_end_index"]),
                              ("test",expected_test,f["test_start_index"],f["test_end_index"])):
            if not n:
                result[split]={"x_shape":[0,len(feature_names)],"y_shape":[0],
                               "ordered_rows_sha256":hashlib.sha256().hexdigest()}
                continue
            x=arrays.allocate((n,len(feature_names)))
            y=arrays.allocate((n,))
            pos=0
            digest=hashlib.sha256()
            cur=cx.execute(f"""SELECT session_index,security_id,{cols},target,label_end_index
                FROM v6_fold_matrix WHERE split=? ORDER BY session_index,security_id""",[split])
            while batch:=cur.fetchmany(2048):
                if pos+len(batch)>n:
                    raise ValueError("FOLD_MATRIX_ROW_BUDGET")
                for i,row in enumerate(batch):
                    ordinal,sid,*values,end=row
                    if (not isinstance(sid,str) or not sid or not lo<=ordinal<=hi
                            or end!=ordinal+h or not all(isfinite(v) for v in values)
                            or values[-1]<-1):
                        raise ValueError("FOLD_MATRIX_INVALID_ROW")
                    digest.update(dataset._digest([ordinal,sid,end]).encode())
                    digest.update(struct.pack("<"+"d"*len(values),*values))
                    x[pos+i]=values[:-1]
                    y[pos+i]=values[-1]
                if not np.isfinite(x[pos:pos+len(batch)]).all() or not np.isfinite(y[pos:pos+len(batch)]).all():
                    raise ValueError("FOLD_MATRIX_NONFINITE_ARRAY")
                pos+=len(batch)
            if pos!=n:
                raise ValueError("FOLD_MATRIX_ROW_COUNT_CHANGED")
            result[split]={"x_shape":[n,len(feature_names)],"y_shape":[n],
                           "ordered_rows_sha256":digest.hexdigest()}
            del x,y
        result["array_bytes_allocated"]=arrays.resident+arrays.spilled
        result["array_bytes_spilled"]=arrays.spilled
    return {"status":"FOLD_ARRAYS_REVIEWED" if expected_train and expected_test else "FOLD_ARRAYS_EMPTY_SPLIT",
            "horizon":h,"fold_index":matrix["fold_index"],"feature_names":feature_names,
            "fold":f,"splits":{k:result[k] for k in ("train","test")},
            "array_bytes_allocated":result["array_bytes_allocated"],
            "array_bytes_spilled":result["array_bytes_spilled"],
            "array_budget_mib":matrix["array_budget_mib"],
            "retrospective_subset_only":True,"prediction_universe_defined":False,
            "feature_history_identity_verified":False,
            "feature_history_subset_required":matrix.get("require_feature_history",False),
            "arrays_retained":False,
            "models_fitted":0,"training_started":False,"approved_for_model_training":False}


def run(dataset_id,*,minimum_train_rows=1000,minimum_test_rows=100,memory="2GB",threads=2,
        require_lineage=False,identity_as_of=None,matrix_request=None):
    for n in (minimum_train_rows,minimum_test_rows):
        if type(n) is not int or n<1:
            raise ValueError("Minimum sample counts must be positive integers")
    if matrix_request is not None and identity_as_of is None:
        raise ValueError("FOLD_MATRIX_REQUIRES_IDENTITY_CUTOFF")
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
    closing_observed=[0]*len(calendar)
    close_path_counts={h:[0]*len(calendar) for h in c["horizons"]}
    identity_counts={h:[0]*len(calendar) for h in c["horizons"]}
    action_free_counts={h:[0]*len(calendar) for h in c["horizons"]}
    feature_history_counts={h:[0]*len(calendar) for h in c["horizons"]}
    feature_lookback=max(FEATURE_LOOKBACK[name] for name in c["features"]) if identity_as_of is not None else None
    identity=None
    matrix=None
    matrix_report=None
    try:
        dataset._install_calendar(cx,calendar)
        if matrix_request is not None:
            matrix=_matrix_request(cx,calendar,c,matrix_request)
        if identity_as_of is not None:
            identity=_install_identity_review(cx,calendar,identity_as_of)
        for month,p in paths.items():
            _check_partition(cx,p,month,c["features"],c["horizons"])
            _check_feature_source(cx,p,source_paths[month],c["features"])
        for month,p in paths.items():
            window=dataset._window(month,months,calendar,max(c["horizons"]))
            if feature_lookback is not None:
                first_index=next(i for i,row in enumerate(calendar) if row[0][:7]==month)
                first_month=calendar[max(0,first_index-feature_lookback)][0][:7]
                window=[m for m in months if first_month<=m<=window[-1]]
            for i,closing,*counts in _check_labels(cx,[paths[k] for k in window],month,c["horizons"],
                                                     identity_review=identity is not None,matrix=matrix,
                                                     feature_lookback=feature_lookback):
                closing_observed[i]=closing
                size=len(c["horizons"])
                for j,h in enumerate(c["horizons"]):
                    close_path_counts[h][i]=counts[j]
                    if identity is not None:
                        identity_counts[h][i],action_free_counts[h][i]=counts[size+2*j:size+2*j+2]
                        feature_history_counts[h][i]=counts[3*size+j]
            src=factor._files_sql([p])
            cols=", ".join(f"count(*) FILTER (WHERE features_complete AND label_{h}d IS NOT NULL)" for h in c["horizons"])
            for row in cx.execute(f"""SELECT session_index,count(*),count(*) FILTER (WHERE features_complete),{cols}
                         FROM {src} GROUP BY session_index""").fetchall():
                i,n,pred_n,*counts=row
                observed[i]=int(n)
                pred[i]=int(pred_n)
                for h,count in zip(c["horizons"],counts):
                    horizon_counts[h][i]=int(count)
        if matrix is not None:
            f=matrix["fold"]
            selected_counts=feature_history_counts if matrix.get("require_feature_history") else action_free_counts
            prefix=_prefix(selected_counts[matrix["horizon"]])
            matrix_report=_review_matrix_arrays(cx,matrix,
                _sum(prefix,f["train_start_index"],f["train_end_index"]),
                _sum(prefix,f["test_start_index"],f["test_end_index"]))
    finally:
        cx.close()
    if dataset.inspect(dataset_id)!=m:
        raise ValueError("Dataset manifest changed during preflight")
    _source_inputs(m)  # source factors must still match the recorded revision
    if m.get("lineage_id") is not None:
        current=factor_preflight(months[0],months[-1],memory=memory,threads=threads)
        if not current["structural_preflight_passed"] or current["evidence_id"]!=m["lineage_id"]:
            raise ValueError("Unified factor lineage changed during preflight")
    if identity is not None:
        from atlaspm.data.quality_review import _ledger_digest
        if _ledger_digest()!=identity["ledger_claims_sha256"]:
            raise ValueError("IDENTITY_REVIEW_LEDGER_CHANGED_DURING_SAMPLE_SCAN")
    prediction_prefix=_prefix(pred)
    folds={}
    totals={}
    for h,counts in horizon_counts.items():
        prefix=_prefix(counts)
        strict_prefix=_prefix(close_path_counts[h])
        if any(a>b for a,b in zip(close_path_counts[h],counts,strict=True)):
            raise ValueError("Closing-bar label count exceeds observed label count")
        rows=[]
        for f in dataset._folds(calendar,h,c["minimum_train_sessions"],c["test_sessions"],c["embargo_sessions"]):
            train=_sum(prefix,f["train_start_index"],f["train_end_index"])
            test=_sum(prefix,f["test_start_index"],f["test_end_index"])
            predictions=_sum(prediction_prefix,f["test_start_index"],f["test_end_index"])
            strict_train=_sum(strict_prefix,f["train_start_index"],f["train_end_index"])
            strict_test=_sum(strict_prefix,f["test_start_index"],f["test_end_index"])
            rows.append({**f,"train_labeled_rows":train,"test_labeled_rows":test,
                         "test_feature_complete_rows":predictions,"test_missing_labels":predictions-test,
                         "sample_count_pass":train>=minimum_train_rows and test>=minimum_test_rows,
                         "closing_path_train_rows":strict_train,"closing_path_test_rows":strict_test,
                         "closing_path_sample_count_pass":strict_train>=minimum_train_rows and strict_test>=minimum_test_rows})
        if identity is not None:
            if any(not 0<=a<=b<=c for a,b,c in zip(action_free_counts[h],identity_counts[h],close_path_counts[h],strict=True)):
                raise ValueError("IDENTITY_PATH_COUNT_INCONSISTENT")
            if any(not 0<=a<=b for a,b in zip(feature_history_counts[h],action_free_counts[h],strict=True)):
                raise ValueError("FEATURE_HISTORY_PATH_COUNT_INCONSISTENT")
            action_prefix=_prefix(action_free_counts[h])
            feature_prefix=_prefix(feature_history_counts[h])
            for row in rows:
                train=_sum(action_prefix,row["train_start_index"],row["train_end_index"])
                test=_sum(action_prefix,row["test_start_index"],row["test_end_index"])
                row.update(reviewed_identity_action_free_train_rows=train,
                           reviewed_identity_action_free_test_rows=test,
                           reviewed_identity_action_free_minimum_pass=train>=minimum_train_rows and test>=minimum_test_rows)
                ft=_sum(feature_prefix,row["train_start_index"],row["train_end_index"])
                fv=_sum(feature_prefix,row["test_start_index"],row["test_end_index"])
                row.update(reviewed_feature_history_train_rows=ft,reviewed_feature_history_test_rows=fv,
                           reviewed_feature_history_minimum_pass=ft>=minimum_train_rows and fv>=minimum_test_rows)
        folds[str(h)]=rows
        totals[str(h)]={"labeled_feature_complete_rows":sum(counts),"fold_count":len(rows),
                        "folds_meeting_sample_minimum":sum(f["sample_count_pass"] for f in rows),
                        "closing_path_labeled_feature_complete_rows":sum(close_path_counts[h]),
                        "rows_excluded_by_closing_path":sum(counts)-sum(close_path_counts[h]),
                        "folds_meeting_closing_path_sample_minimum":sum(f["closing_path_sample_count_pass"] for f in rows)}
        if identity is not None:
            totals[str(h)].update(
                reviewed_identity_path_rows=sum(identity_counts[h]),
                rows_excluded_by_identity_path=sum(close_path_counts[h])-sum(identity_counts[h]),
                rows_excluded_by_visible_actions=sum(identity_counts[h])-sum(action_free_counts[h]),
                reviewed_identity_action_free_rows=sum(action_free_counts[h]),
                reviewed_feature_history_rows=sum(feature_history_counts[h]),
                rows_excluded_by_feature_history=sum(action_free_counts[h])-sum(feature_history_counts[h]),
                folds_meeting_reviewed_feature_history_minimum=sum(f["reviewed_feature_history_minimum_pass"] for f in rows),
                folds_meeting_reviewed_identity_action_free_minimum=sum(
                    f["reviewed_identity_action_free_minimum_pass"] for f in rows))
    report={"schema":SCHEMA,**v4.GUARDS,"dataset_id":dataset_id,
            "status":"STRUCTURAL_PREFLIGHT_COMPLETE_TRAINING_BLOCKED",
            "observed_rows":sum(observed),"feature_complete_rows":sum(pred),
            "observed_session_closing_bar_rows":sum(closing_observed),
            "rows_missing_session_closing_bar":sum(observed)-sum(closing_observed),
            "closing_path_rule":"All h+1 observed bars end at their session close; features complete; observed label present",
            "closing_path_counts_are_diagnostic":True,
            "calendar_sessions":len(calendar),"sessions_without_observations":sum(n==0 for n in observed),
            "minimum_train_rows":minimum_train_rows,"minimum_test_rows":minimum_test_rows,
            "label_paths_reconciled":True,"feature_source_reconciled":True,
            "lineage_id":m.get("lineage_id"),"approved_for_backtest":False,
            "point_in_time_verified":False,"broker_order_submission_enabled":False,
            "model_registry_admission":False,"horizons":totals,"folds":folds,
            "blockers":m["blockers"],"models_fitted":0,
            "warning":"Fold counts are structural only. Unverified security identity, actions, rights and feature timing prevent formal training."}
    if identity is not None:
        report["identity_path_review"]=identity
        report["feature_history_review"]={"maximum_lookback_sessions":feature_lookback,
                "required_observations":feature_lookback+1,
                "scope":"within_dataset_only",
                "rule":"complete closing observations, one reviewed security ID, no visible action",
                "uses_retrospective_evidence":True,"feature_values_recomputed":False,
                "independent_feature_identity_verified":False}
        blockers=set(report["blockers"])
        if identity["daily_unresolved_claim_observations"] or identity["retrospective_unresolved_claims"]:
            blockers.add("UNRESOLVED_IDENTITY_OR_ACTION_CLAIMS")
        if identity["retrospective_disagreements"]:
            blockers.add("HISTORICAL_IDENTITY_REVISION_DISAGREEMENT")
        for h,counts in totals.items():
            if not counts["fold_count"] or counts["folds_meeting_reviewed_identity_action_free_minimum"]!=counts["fold_count"]:
                blockers.add("INSUFFICIENT_REVIEWED_IDENTITY_ACTION_FREE_SAMPLES:"+h)
            if not counts["fold_count"] or counts["folds_meeting_reviewed_feature_history_minimum"]!=counts["fold_count"]:
                blockers.add("INSUFFICIENT_REVIEWED_FEATURE_HISTORY_SAMPLES:"+h)
        report["blockers"]=sorted(blockers)
    if matrix_report is not None:
        report["fold_array_review"]=matrix_report
        if matrix_report["status"]!="FOLD_ARRAYS_REVIEWED":
            report["blockers"]=sorted(set(report["blockers"])|{"EMPTY_REVIEWED_FOLD_ARRAY_SPLIT"})
    filename=("fold_array_preflight.json" if matrix_report is not None else
              "identity_preflight.json" if identity is not None else "preflight.json")
    path=dataset.BASE/dataset_id[:24]/filename
    report["report_path"]=str(private_path(path))
    ev._json(path,report)
    return report
