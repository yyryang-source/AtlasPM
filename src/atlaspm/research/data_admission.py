"""Research-facing real-data boundary. Existing synthetic training is unchanged."""


def inspect_real_factors(start_month, end_month, *, memory="256MB", threads=1):
    """Return actual local data diagnostics, not a model-training panel."""
    from atlaspm.data.unified_pipeline import factor_preflight
    return factor_preflight(start_month, end_month, memory=memory, threads=threads)


def require_real_training_admission():
    """No independently certified real-data admission issuer exists yet.

    Do not accept a caller's boolean, self-authored manifest or exploratory
    receipt as authority. Preserve the trainer's existing ValueError contract.
    """
    raise ValueError("REAL_TRAINING_DATA_NOT_APPROVED")


def build_research_dataset(start_month, end_month, *, memory="256MB", threads=1, **options):
    """Canonical exploratory bridge using V6 storage, labels and folds."""
    from atlaspm.research.real_dataset_v6 import build
    if "verify_lineage" in options:
        raise ValueError("Unified lineage verification cannot be overridden")
    return build(start_month,end_month,memory=memory,threads=threads,
                 verify_lineage=True,**options)


def inspect_research_dataset(dataset_id, *, memory="256MB", threads=1, **options):
    from atlaspm.research.real_dataset_preflight_v61 import run
    if "require_lineage" in options:
        raise ValueError("Unified lineage verification cannot be overridden")
    return run(dataset_id,memory=memory,threads=threads,require_lineage=True,**options)


def _month_index(value):
    from atlaspm.data.hf_stock5m import validate_month
    y,m=map(int,validate_month(value).split("-"))
    return y*12+m-1


def _month_name(index):
    y,m=divmod(index,12)
    return f"{y:04d}-{m+1:02d}"


def _archive_inventory(start,end):
    """Reuse bounded archive planner in chunks; inspect paths, not price rows."""
    from atlaspm.data.unified import archive_plan
    first,last=_month_index(start),_month_index(end)
    if first>last or last-first>=600:
        raise ValueError("DATA_PREPARATION_INVENTORY_RANGE")
    rows=[]
    for i in range(first,last+1,120):
        rows.extend(archive_plan(_month_name(i),_month_name(min(i+119,last)))["months"])
    if [r["month"] for r in rows]!=[_month_name(i) for i in range(first,last+1)]:
        raise ValueError("DATA_PREPARATION_INVENTORY_SCOPE")
    if any(r["within_advertised_archive"] is not True for r in rows):
        raise ValueError("DATA_PREPARATION_OUTSIDE_ARCHIVE")
    return rows


def _spans(rows,field):
    spans=[]
    for row in rows:
        present=(row["raw_present"] or row["bars_present"]) if field=="source" else row["factor_present"]
        if present:
            if spans and _month_index(row["month"])==_month_index(spans[-1]["end_month"])+1:
                spans[-1]["end_month"]=row["month"]
                spans[-1]["months"]+=1
            else:
                spans.append({"start_month":row["month"],"end_month":row["month"],"months":1})
    return spans


def real_data_inventory(start_month=None,end_month=None):
    """Find contiguous local source/factor ranges without scanning Parquet."""
    from atlaspm.data.hf_stock5m import EARLIEST_MONTH,LATEST_ADVERTISED_MONTH
    from atlaspm.research.training_registry import FLAGS
    rows=_archive_inventory(start_month or EARLIEST_MONTH,end_month or LATEST_ADVERTISED_MONTH)
    return {**FLAGS,"status":"LOCAL_DATA_INVENTORY_COMPLETE",
            "source":"hf","session":"rth","months_examined":len(rows),
            "raw_month_count":sum(r["raw_present"] for r in rows),
            "converted_month_count":sum(r["bars_present"] for r in rows),
            "factor_month_count":sum(r["factor_present"] for r in rows),
            "source_spans":_spans(rows,"source"),"factor_spans":_spans(rows,"factor"),
            "presence_only":True,"network_downloads":0,"model_registry_admission":False}


def prepare_real_research(start_month,end_month,*,execute=False,warmup_months=2,
                          minimum_train_sessions=60,test_sessions=20,embargo_sessions=1,
                          minimum_train_rows=1000,minimum_test_rows=100,
                          memory="256MB",threads=1):
    """One local source-to-label pipeline, with explicit write permission.

    Existing builders own conversion, quality checks, factor materialization,
    lineage, sample generation and purged-fold validation. No duplicate lake,
    new trainer, network download, synthetic substitution or authority token.
    Warmup months are an explicit preparation scope, not completeness proof.
    """
    import os,re
    from pathlib import Path
    from atlaspm.data.hf_stock5m import EARLIEST_MONTH,LATEST_ADVERTISED_MONTH
    from atlaspm.data.unified_pipeline import maintain
    from atlaspm.data.market_db import private_path
    from atlaspm.research import real_dataset_v6 as dataset
    from atlaspm.research.training_registry import FLAGS

    if (type(execute) is not bool or type(warmup_months) is not int or not 0<=warmup_months<=36
            or type(threads) is not int or not 1<=threads<=8
            or not isinstance(memory,str) or not re.fullmatch(r"[1-9][0-9]{0,5}(?:MB|GB)",memory)
            or any(type(v) is not int or not 1<=v<=10_000_000
                   for v in (minimum_train_rows,minimum_test_rows))):
        raise ValueError("DATA_PREPARATION_RESOURCE_OR_SAMPLE_POLICY")
    mb=int(memory[:-2])*(1024 if memory.endswith("GB") else 1)
    if not 128<=mb<=16384:
        raise ValueError("DATA_PREPARATION_MEMORY_LIMIT")
    first,last=_month_index(start_month),_month_index(end_month)
    if not _month_index(EARLIEST_MONTH)<=first<=last<=_month_index(LATEST_ADVERTISED_MONTH) or last-first>=24:
        raise ValueError("DATA_PREPARATION_TARGET_LIMIT_24_MONTHS")
    months,config=dataset._config(start_month,end_month,dataset.DEFAULT_FEATURES,(1,5,21),
                                  minimum_train_sessions,test_sessions,embargo_sessions)
    warm_first=max(_month_index(EARLIEST_MONTH),first-warmup_months)
    warm_start=_month_name(warm_first)
    rows=_archive_inventory(warm_start,end_month)
    missing=[r["month"] for r in rows if not (r["bars_present"] or r["raw_present"])]
    calendar=dataset._calendar(months)
    expected_folds={str(h):len(dataset._folds(calendar,h,minimum_train_sessions,test_sessions,embargo_sessions))
                    for h in config["horizons"]}
    result={**FLAGS,"status":"DATA_PREPARATION_PLAN_READY","config":config,
            "target_month_count":len(months),"warmup_requested_months":warmup_months,
            "warmup_start_month":warm_start,"warmup_available_archive_months":first-warm_first,
            "missing_source_months":missing,"expected_calendar_folds":expected_folds,
            "resource_policy":{"memory":memory,"threads":threads},
            "maintenance_started":False,"dataset_id":None,"models_fitted":0,
            "training_started":False,"model_registry_admission":False,"network_downloads":0,
            "blockers":[]}
    if missing:
        result["blockers"].append("MISSING_LOCAL_SOURCE_MONTHS")
    if not all(expected_folds.values()):
        result["blockers"].append("INSUFFICIENT_CALENDAR_FOR_REQUESTED_FOLDS")
    if result["blockers"]:
        result["status"]="DATA_PREPARATION_INPUT_BLOCKED"
        return result
    if not execute:
        return result
    # Serialize this entry point only; operator must stop direct builders and
    # downloaders that modify the same input/derived paths. An abandoned lock
    # is intentionally not auto-cleared after a crash.
    root=Path("local_data")
    lock_dir=root/"training"/"v6"
    if any(p.is_symlink() for p in (root,root/"training",lock_dir,lock_dir/".prepare.lock")):
        raise ValueError("DATA_PREPARATION_PRIVATE_PATH_REQUIRED")
    lock=private_path(lock_dir/".prepare.lock")
    lock.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    stage="DERIVED_DATA_MAINTENANCE"
    try:
        result["maintenance_started"]=True
        maintained=maintain(warm_start,end_month,execute=True,memory=memory,threads=threads)
        result["maintenance_months"]=[{k:v for k,v in row.items() if k in
                                      {"month","bars","audit","factors","status"}}
                                      for row in maintained["months"]]
        if maintained["all_requested_months_processed"] is not True:
            result["status"]="DATA_PREPARATION_MAINTENANCE_BLOCKED"
            result["blockers"].append("LOCAL_MAINTENANCE_FAILED")
            return result
        stage="DATASET_BUILD"
        built=build_research_dataset(start_month,end_month,memory=memory,threads=threads,
                      minimum_train_sessions=minimum_train_sessions,test_sessions=test_sessions,
                      embargo_sessions=embargo_sessions)
        if built.get("status")!="RESEARCH_DATASET_READY_TRAINING_BLOCKED" or not built.get("lineage_id"):
            result["status"]="DATA_PREPARATION_DATASET_BLOCKED"
            result["blockers"].append("DATASET_OR_LINEAGE_NOT_READY")
            return result
        result["dataset_id"]=built["dataset_id"]
        result["lineage_id"]=built["lineage_id"]
        stage="EXACT_LABEL_AND_FOLD_PREFLIGHT"
        checked=inspect_research_dataset(built["dataset_id"],memory=memory,threads=threads,
                         minimum_train_rows=minimum_train_rows,minimum_test_rows=minimum_test_rows)
        if (checked.get("dataset_id")!=built["dataset_id"] or checked.get("lineage_id")!=built["lineage_id"]
                or checked.get("label_paths_reconciled") is not True
                or checked.get("feature_source_reconciled") is not True
                or checked.get("status")!="STRUCTURAL_PREFLIGHT_COMPLETE_TRAINING_BLOCKED"):
            raise ValueError("DATA_PREPARATION_PREFLIGHT_CONTEXT_MISMATCH")
        blockers=set(built.get("blockers",[]))|set(checked.get("blockers",[]))
        blockers.update(built.get("factor_lineage",{}).get("blockers",[]))
        if checked["sessions_without_observations"]:
            blockers.add("MISSING_DATASET_SESSIONS")
        if not checked["feature_complete_rows"]:
            blockers.add("NO_COMPLETE_FEATURE_ROWS")
        if set(checked["horizons"])!=set(expected_folds):
            raise ValueError("DATA_PREPARATION_HORIZON_MISMATCH")
        counts_ok=True
        closing_counts_ok=True
        for h,counts in checked["horizons"].items():
            if counts["fold_count"]!=expected_folds[h]:
                raise ValueError("DATA_PREPARATION_FOLD_COUNT_MISMATCH")
            if not counts["fold_count"] or counts["folds_meeting_sample_minimum"]!=counts["fold_count"]:
                counts_ok=False
                blockers.add("INSUFFICIENT_FOLD_SAMPLE_COUNTS:"+h)
            if not counts["fold_count"] or counts["folds_meeting_closing_path_sample_minimum"]!=counts["fold_count"]:
                closing_counts_ok=False
                blockers.add("INSUFFICIENT_CLOSING_PATH_FOLD_SAMPLES:"+h)
        blockers.add("INDEPENDENT_REAL_TRAINING_ADMISSION_REQUIRED")
        result.update(status="RESEARCH_DATASET_PREPARED_TRAINING_BLOCKED",
                      observed_rows=checked["observed_rows"],feature_complete_rows=checked["feature_complete_rows"],
                      calendar_sessions=checked["calendar_sessions"],
                      sessions_without_observations=checked["sessions_without_observations"],
                      horizon_sample_counts=checked["horizons"],all_folds_meet_sample_minimum=counts_ok,
                      all_folds_meet_closing_path_minimum=closing_counts_ok,
                      rows_missing_session_closing_bar=checked["rows_missing_session_closing_bar"],
                      feature_source_reconciled=True,label_paths_reconciled=True,
                      structural_preflight_complete=True,blockers=sorted(blockers))
        return result
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:
        result.update(status="DATA_PREPARATION_STAGE_BLOCKED",failed_stage=stage,
                      error_type=type(exc).__name__,blockers=["LOCAL_PIPELINE_STAGE_FAILED"])
        return result
    finally:
        lock.unlink(missing_ok=True)


def main():
    import argparse,json
    parser=argparse.ArgumentParser(description="Prepare actual local research data; never grants formal training admission")
    parser.add_argument("action",choices=("inventory","prepare","identity-review"))
    parser.add_argument("--dataset-id")
    parser.add_argument("--as-of",help="Explicit retrospective evidence cutoff YYYY-MM-DD")
    parser.add_argument("--start-month")
    parser.add_argument("--end-month")
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--warmup-months",type=int,default=2)
    parser.add_argument("--memory",default="256MB")
    parser.add_argument("--threads",type=int,default=1)
    parser.add_argument("--minimum-train-sessions",type=int,default=60)
    parser.add_argument("--test-sessions",type=int,default=20)
    parser.add_argument("--embargo-sessions",type=int,default=1)
    parser.add_argument("--minimum-train-rows",type=int,default=1000)
    parser.add_argument("--minimum-test-rows",type=int,default=100)
    args=parser.parse_args()
    if args.action=="identity-review":
        if args.execute or not args.dataset_id or not args.as_of or args.start_month or args.end_month:
            parser.error("identity-review requires --dataset-id and --as-of; no --execute or month flags")
        out=inspect_research_dataset(args.dataset_id,memory=args.memory,threads=args.threads,
              minimum_train_rows=args.minimum_train_rows,minimum_test_rows=args.minimum_test_rows,
              identity_as_of=args.as_of)
    elif args.dataset_id or args.as_of:
        parser.error("--dataset-id and --as-of are scoped to identity-review")
    elif args.action=="inventory":
        if args.execute:
            parser.error("inventory is read-only")
        out=real_data_inventory(args.start_month,args.end_month)
    else:
        if not args.start_month or not args.end_month:
            parser.error("prepare requires explicit start and end month")
        out=prepare_real_research(args.start_month,args.end_month,execute=args.execute,
            warmup_months=args.warmup_months,memory=args.memory,threads=args.threads,
            minimum_train_sessions=args.minimum_train_sessions,test_sessions=args.test_sessions,
            embargo_sessions=args.embargo_sessions,minimum_train_rows=args.minimum_train_rows,
            minimum_test_rows=args.minimum_test_rows)
    print(json.dumps(out,indent=2,allow_nan=False))
    return 0 if out["status"] in {"LOCAL_DATA_INVENTORY_COMPLETE","DATA_PREPARATION_PLAN_READY",
                                  "RESEARCH_DATASET_PREPARED_TRAINING_BLOCKED",
                                  "STRUCTURAL_PREFLIGHT_COMPLETE_TRAINING_BLOCKED"} else 2


if __name__=="__main__":
    raise SystemExit(main())
