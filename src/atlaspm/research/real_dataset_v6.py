"""V6 real-data research dataset adapter. Never authorizes model training.

Build source-isolated Parquet sample partitions, exact NYSE-session labels and
purged walk-forward metadata. The existing synthetic trainer remains isolated.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from atlaspm.data import factor_db as factor
from atlaspm.data import hf_stock5m as hf
from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data import hf_stock5m_session_v4 as v4
from atlaspm.data.hf_stock5m_certification_v5 import fingerprint
from atlaspm.data.market_db import private_path
from atlaspm.research.training_dataset import HORIZONS

BASE = Path("local_data/training/v6")
VERSION = "atlaspm_real_research_dataset_v6.1"
DEFAULT_FEATURES = ("mom_5d","mom_21d","volatility_21d","price_vs_sma_20d","volume_ratio_20d")


def _digest(obj):
    return ev._sha(json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode())


def _stamp(path):
    item=fingerprint(path)
    item["path"]=str(private_path(path).relative_to(Path.cwd().resolve()))
    return item


def _month_list(start,end):
    start,end=hf.validate_month(start),hf.validate_month(end)
    months=hf.months(start,end,0)
    if not months or months[0]!=start or months[-1]!=end:
        raise ValueError("Select a complete range within the advertised HF archive")
    return months


def _config(start,end,features,horizons,min_train,test,embargo):
    months=_month_list(start,end)
    features=list(features)
    horizons=list(horizons)
    if not features or len(set(features))!=len(features) or any(x not in factor.FEATURES for x in features):
        raise ValueError("Features must be unique catalog factor names")
    if not horizons or len(set(horizons))!=len(horizons) or any(type(x) is not int or x not in HORIZONS for x in horizons):
        raise ValueError("Unsupported or duplicate label horizons")
    for x,lo in ((min_train,1),(test,1),(embargo,0)):
        if type(x) is not int or x<lo or x>10000:
            raise ValueError("Invalid fold configuration")
    return months,{"version":VERSION,"source":"hf","session":"rth","months":months,
                   "features":features,"horizons":sorted(horizons),
                   "minimum_train_sessions":min_train,"test_sessions":test,
                   "embargo_sessions":embargo}


def _inputs(months):
    paths={}
    missing=[]
    for month in months:
        p=factor._path(month,"hf","rth","factors")
        meta=factor._read_meta(p.with_suffix(".json"))
        if not p.is_file() or not meta:
            missing.append(month)
            continue
        if (meta.get("version")!=factor.VERSION or meta.get("month")!=month
                or meta.get("source")!="hf" or meta.get("session")!="rth"):
            raise ValueError(f"Invalid factor source manifest: {month}")
        paths[month]=p
    return paths,missing


def _calendar(months):
    rows=[(d.isoformat(),int(o),int(c)) for m in months for d,o,c,_ in v4.calendar_rows(m)]
    if not rows or any(a[0]>=b[0] for a,b in zip(rows,rows[1:])):
        raise ValueError("Invalid or duplicate NYSE sessions")
    return rows


def _folds(calendar,horizon,min_train,test,embargo):
    # Same expanding-window policy as the synthetic engine: training label
    # end strictly precedes test start minus embargo. Exclude final censored
    # signal dates; folds reference calendar ordinals, not observed row counts.
    n=len(calendar)
    result=[]
    for start in range(min_train+horizon+embargo,n-horizon,test):
        stop=min(start+test,n-horizon)
        last_train=start-horizon-embargo-1
        result.append({"train_start_index":0,"train_end_index":last_train,
                       "latest_training_label_end_index":last_train+horizon,
                       "test_start_index":start,"test_end_index":stop-1,
                       "train_end_date":calendar[last_train][0],
                       "test_start_date":calendar[start][0],
                       "test_end_date":calendar[stop-1][0]})
    return result


def _install_calendar(cx,calendar):
    cx.execute("""CREATE OR REPLACE TEMP TABLE v6_sessions(
        trade_date DATE PRIMARY KEY, session_index INTEGER, open_ms BIGINT, close_ms BIGINT)""")
    cx.executemany("INSERT INTO v6_sessions VALUES (?,?,?,?)",
                   [(d,i,o,c) for i,(d,o,c) in enumerate(calendar)])


def _validate_partition(cx,p,month):
    src=factor._files_sql([p])
    n,bad=cx.execute(f"""SELECT count(*),count(*) FILTER (WHERE NOT coalesce(
        d.symbol IS NOT NULL AND length(d.symbol) BETWEEN 1 AND 32
        AND strftime(d.trade_date,'%Y-%m')=? AND s.trade_date IS NOT NULL
        AND isfinite(d.close) AND d.close>0
        AND d.last_observed_bar_ms>=s.open_ms
        AND d.last_observed_bar_ms+300000<=s.close_ms
        AND d.last_observed_bar_ms%300000=0,false))
        FROM {src} d LEFT JOIN v6_sessions s ON d.trade_date=s.trade_date""",[month]).fetchone()
    dup=cx.execute(f"SELECT 1 FROM {src} GROUP BY symbol,trade_date HAVING count(*)>1 LIMIT 1").fetchone()
    if not n or bad or dup:
        raise ValueError(f"Factor partition fails V6 calendar/price/key validation: {month}; invalid={bad}, duplicates={bool(dup)}")
    return int(n)


def _sample_sql(paths,month,features,horizons):
    src=factor._files_sql(paths)
    names=", ".join(f"d.{f}" for f in features)
    projection=", ".join(features)
    complete=" AND ".join(f"coalesce(isfinite({f}),false)" for f in features)
    # Lead on a symbol is only usable if the resulting calendar ordinal is
    # exactly h sessions later. No forward fill, no bridging missing sessions.
    lead=", ".join(f"lead(close,{h}) OVER w AS p{h}, lead(session_index,{h}) OVER w AS i{h}" for h in horizons)
    labels=[]
    for h in horizons:
        cond=f"i{h}=session_index+{h} AND isfinite(p{h}/close)"
        labels.extend([f"CASE WHEN {cond} THEN p{h}/close-1 END AS label_{h}d",
                       f"CASE WHEN {cond} THEN i{h} END AS label_end_index_{h}d"])
    return f"""
      WITH base AS (
        SELECT d.symbol,d.trade_date,d.close,d.last_observed_bar_ms,
               s.session_index,s.close_ms,{names}
        FROM {src} d JOIN v6_sessions s ON d.trade_date=s.trade_date
      ), lagged AS (
        SELECT *,{lead} FROM base WINDOW w AS (PARTITION BY symbol ORDER BY session_index)
      )
      SELECT symbol,trade_date,session_index,close,
             last_observed_bar_ms+300000 AS last_bar_available_at_ms,
             close_ms AS earliest_assumed_feature_time_ms,
             ({complete}) AS features_complete,{projection},
             {", ".join(labels)}
      FROM lagged WHERE strftime(trade_date,'%Y-%m')='{month}'
      ORDER BY session_index,symbol
    """


def _counts(cx,path,horizons):
    src=factor._files_sql([path])
    expr=", ".join(f"count(label_{h}d),count(*) FILTER (WHERE features_complete AND label_{h}d IS NOT NULL)" for h in horizons)
    row=cx.execute(f"SELECT count(*),count(*) FILTER (WHERE features_complete),{expr} FROM {src}").fetchone()
    return {"rows":int(row[0]),"feature_complete_rows":int(row[1]),
            "labels":{str(h):{"available":int(row[2+2*i]),"feature_and_label_complete":int(row[3+2*i])}
                      for i,h in enumerate(horizons)}}


def _window(month,months,calendar,horizon):
    # Only load current signal month and the months containing the following
    # horizon calendar sessions. Avoid reading the entire archive per partition.
    indices=[i for i,r in enumerate(calendar) if r[0][:7]==month]
    if not indices:
        raise ValueError("Calendar month missing")
    last=calendar[min(len(calendar)-1,indices[-1]+horizon)][0][:7]
    return [m for m in months if month<=m<=last]


def build(start,end,*,features=DEFAULT_FEATURES,horizons=(1,5,21),
          minimum_train_sessions=60,test_sessions=20,embargo_sessions=1,
          memory="2GB",threads=2,plan_only=False):
    months,config=_config(start,end,features,horizons,minimum_train_sessions,test_sessions,embargo_sessions)
    inputs,missing=_inputs(months)
    report={"schema":VERSION,**v4.GUARDS,"mode":"real_unverified_research",
            "synthetic_fixture":False,"config":config,"missing_factor_months":missing,
            "status":"MISSING_FACTOR_PARTITIONS" if missing else "PLAN_READY",
            "blockers":["HISTORICAL_SECURITY_IDENTITY_UNVERIFIED","CORPORATE_ACTIONS_UNVERIFIED",
                        "FEATURE_AVAILABILITY_UNVERIFIED","PRICE_ADJUSTMENT_SEMANTICS_UNVERIFIED",
                        "HISTORICAL_COVERAGE_UNVERIFIED","DATA_RIGHTS_UNVERIFIED"],
            "label_definition":"Observed close-to-close price change, not total return or executable strategy return."}
    if missing or plan_only:
        return report
    calendar=_calendar(months)
    hashes={m:{"data":_stamp(p),"meta":_stamp(p.with_suffix(".json"))} for m,p in inputs.items()}
    key=_digest({"config":config,"calendar_sha256":_digest(calendar),"inputs":hashes})
    root=private_path(BASE/key[:24])
    root.mkdir(parents=True,exist_ok=True)
    manifest=root/"manifest.json"
    cx=factor._db(memory,threads)
    partitions=[]
    try:
        _install_calendar(cx,calendar)
        for month,p in inputs.items():
            _validate_partition(cx,p,month)
        for month in months:
            out=private_path(root/(month+".parquet"))
            meta=out.with_suffix(".json")
            prev=factor._read_meta(meta)
            if out.is_file() and prev and prev.get("dataset_id")==key and prev.get("sha256")==_stamp(out)["sha256"]:
                stats=_counts(cx,out,config["horizons"])
                partitions.append({"month":month,"dataset_id":key,"path":str(out.relative_to(Path.cwd().resolve())),
                                   "sha256":prev["sha256"],**stats})
                continue
            stage=out.with_suffix(".parquet.part")
            try:
                paths=[inputs[m] for m in _window(month,months,calendar,max(config["horizons"]))]
                sql=_sample_sql(paths,month,config["features"],config["horizons"])
                cx.execute(f"COPY ({sql}) TO {factor._quoted(stage)} (FORMAT PARQUET,COMPRESSION ZSTD)")
                stats=_counts(cx,stage,config["horizons"])
                if not stats["rows"]:
                    raise ValueError(f"Empty sample partition: {month}")
                digest=_stamp(stage)["sha256"]
                stage.replace(out)
                item={"month":month,"dataset_id":key,"path":str(out.relative_to(Path.cwd().resolve())),
                      "sha256":digest,**stats}
                ev._json(meta,item)
                partitions.append(item)
            finally:
                stage.unlink(missing_ok=True)
        if any(_stamp(p)!=hashes[m]["data"] or _stamp(p.with_suffix(".json"))!=hashes[m]["meta"] for m,p in inputs.items()):
            raise ValueError("Factor input changed during build; no dataset publication")
    finally:
        cx.close()
    report.update({"status":"RESEARCH_DATASET_READY_TRAINING_BLOCKED","dataset_id":key,
                   "inputs":hashes,"calendar_sha256":_digest(calendar),"sessions":len(calendar),
                   "partitions":partitions,"rows":sum(x["rows"] for x in partitions),
                   "feature_complete_rows":sum(x["feature_complete_rows"] for x in partitions),
                   "folds":{str(h):_folds(calendar,h,minimum_train_sessions,test_sessions,embargo_sessions)
                            for h in config["horizons"]},
                   "manifest_path":str(manifest)})
    ev._json(manifest,report)
    ev._json(BASE/"latest.json",report)
    return report


def inspect(dataset_id):
    if not isinstance(dataset_id,str) or not re.fullmatch("[0-9a-f]{64}",dataset_id):
        raise ValueError("Use full dataset ID")
    path=private_path(BASE/dataset_id[:24]/"manifest.json",exists=True)
    m=json.loads(path.read_text(encoding="utf-8"))
    if m.get("schema")!=VERSION or m.get("dataset_id")!=dataset_id:
        raise ValueError("Invalid dataset manifest")
    if _digest({"config":m["config"],"calendar_sha256":m["calendar_sha256"],"inputs":m["inputs"]})!=dataset_id:
        raise ValueError("Dataset manifest identity mismatch")
    if [p["month"] for p in m["partitions"]]!=m["config"]["months"]:
        raise ValueError("Dataset partition inventory mismatch")
    for p in m["partitions"]:
        expected=path.parent/(p["month"]+".parquet")
        if (p["dataset_id"]!=dataset_id or private_path(Path(p["path"]))!=expected
                or _stamp(expected)["sha256"]!=p["sha256"]):
            raise ValueError("Dataset partition integrity failure")
    # Never propagate flags that may have been edited in a local JSON file.
    return {**m,**v4.GUARDS,"mode":"real_unverified_research","synthetic_fixture":False,
            "status":"RESEARCH_DATASET_READY_TRAINING_BLOCKED"}
