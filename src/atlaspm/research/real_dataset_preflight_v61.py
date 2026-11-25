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


def _check_labels(cx,paths,month,horizons,*,identity_review=False):
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
    checks=[]
    for h in horizons:
        expected=f"CASE WHEN n{h}={h+1} AND isfinite(p{h}/close) THEN p{h}/close-1 END"
        checks.append(f"label_{h}d IS DISTINCT FROM ({expected})")
    # Only aggregate integer counts per session in Python. The independent
    # partition check already verified assumed feature time against calendar.
    strict=", ".join(f"count(*) FILTER (WHERE features_complete AND label_{h}d IS NOT NULL AND close_n{h}={h+1})" for h in horizons)
    if identity_review:
        for h in horizons:
            condition=f"features_complete AND label_{h}d IS NOT NULL AND close_n{h}={h+1} AND id_n{h}={h+1} AND id_min{h}=id_max{h}"
            strict+=f",count(*) FILTER (WHERE {condition}),count(*) FILTER (WHERE {condition} AND act_n{h}=0)"
    counts=cx.execute(f"""WITH recalculated AS (SELECT *,{", ".join(expr)} FROM {src})
        SELECT session_index,count(*) FILTER (WHERE {" OR ".join(checks)}),
               count(*) FILTER (WHERE last_bar_available_at_ms=earliest_assumed_feature_time_ms),{strict}
        FROM recalculated WHERE strftime(trade_date,'%Y-%m')=?
        GROUP BY session_index ORDER BY session_index""",[month]).fetchall()
    mismatch=sum(row[1] for row in counts)
    if mismatch:
        raise ValueError(f"Label path reconciliation failed for {month}: {mismatch} rows")
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


def run(dataset_id,*,minimum_train_rows=1000,minimum_test_rows=100,memory="2GB",threads=2,
        require_lineage=False,identity_as_of=None):
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
    closing_observed=[0]*len(calendar)
    close_path_counts={h:[0]*len(calendar) for h in c["horizons"]}
    identity_counts={h:[0]*len(calendar) for h in c["horizons"]}
    action_free_counts={h:[0]*len(calendar) for h in c["horizons"]}
    identity=None
    try:
        dataset._install_calendar(cx,calendar)
        if identity_as_of is not None:
            identity=_install_identity_review(cx,calendar,identity_as_of)
        for month,p in paths.items():
            _check_partition(cx,p,month,c["features"],c["horizons"])
            _check_feature_source(cx,p,source_paths[month],c["features"])
        for month,p in paths.items():
            window=dataset._window(month,months,calendar,max(c["horizons"]))
            for i,closing,*counts in _check_labels(cx,[paths[k] for k in window],month,c["horizons"],
                                                     identity_review=identity is not None):
                closing_observed[i]=closing
                size=len(c["horizons"])
                for j,h in enumerate(c["horizons"]):
                    close_path_counts[h][i]=counts[j]
                    if identity is not None:
                        identity_counts[h][i],action_free_counts[h][i]=counts[size+2*j:size+2*j+2]
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
            action_prefix=_prefix(action_free_counts[h])
            for row in rows:
                train=_sum(action_prefix,row["train_start_index"],row["train_end_index"])
                test=_sum(action_prefix,row["test_start_index"],row["test_end_index"])
                row.update(reviewed_identity_action_free_train_rows=train,
                           reviewed_identity_action_free_test_rows=test,
                           reviewed_identity_action_free_minimum_pass=train>=minimum_train_rows and test>=minimum_test_rows)
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
        blockers=set(report["blockers"])
        if identity["daily_unresolved_claim_observations"] or identity["retrospective_unresolved_claims"]:
            blockers.add("UNRESOLVED_IDENTITY_OR_ACTION_CLAIMS")
        if identity["retrospective_disagreements"]:
            blockers.add("HISTORICAL_IDENTITY_REVISION_DISAGREEMENT")
        for h,counts in totals.items():
            if not counts["fold_count"] or counts["folds_meeting_reviewed_identity_action_free_minimum"]!=counts["fold_count"]:
                blockers.add("INSUFFICIENT_REVIEWED_IDENTITY_ACTION_FREE_SAMPLES:"+h)
        report["blockers"]=sorted(blockers)
    path=dataset.BASE/dataset_id[:24]/("identity_preflight.json" if identity is not None else "preflight.json")
    report["report_path"]=str(private_path(path))
    ev._json(path,report)
    return report
