"""V6.3-6.5 bounded synthetic search, ensemble and lifecycle orchestration.

Reuse the training engine, tournament, ensemble and monitor. Selection never
sees holdout outcomes. No real data approval, registry promotion or orders.
"""
from __future__ import annotations

import argparse
from itertools import product
import json
from math import prod
from pathlib import Path
from random import Random

from atlaspm.research.training_tournament import tournament, project_features, _trial_config, _sha
from atlaspm.research.training_dataset import validate_panel_metadata
from atlaspm.research.training_engine import train, _metrics
from atlaspm.research.training_registry import FLAGS

MAX_GRID=4096
MAX_TRIALS=32


def search_candidates(spaces,config,*,budget=12,seed=42):
    """Seeded, model-balanced sampling from finite predeclared grids."""
    if type(budget) is not int or not 2<=budget<=MAX_TRIALS or type(seed) is not int or not 0<=seed<2**31:
        raise ValueError("INVALID_SEARCH_BUDGET_OR_SEED")
    if not isinstance(spaces,list) or not spaces or len(spaces)>budget:
        raise ValueError("SEARCH_BUDGET_MUST_COVER_MODEL_SPACES")
    buckets=[]
    seen=set()
    size=0
    for space in spaces:
        if not isinstance(space,dict) or set(space)!={"model","grid"} or not isinstance(space["grid"],dict):
            raise ValueError("INVALID_SEARCH_SPACE")
        name,grid=space["model"],space["grid"]
        if name in seen:
            raise ValueError("DUPLICATE_SEARCH_MODEL")
        seen.add(name)
        if any(not isinstance(k,str) or not isinstance(v,list) or not v for k,v in grid.items()):
            raise ValueError("INVALID_SEARCH_GRID")
        size+=prod(len(v) for v in grid.values())
        if size>MAX_GRID:
            raise ValueError("SEARCH_GRID_LIMIT")
        keys=sorted(grid)
        bucket=[]
        identities=set()
        for values in product(*(grid[k] for k in keys)):
            candidate={"model":name}
            for k,v in zip(keys,values):
                if k in ("ridge_alpha","sequence_epochs","features"):
                    candidate[k]=v
                else:
                    candidate.setdefault("parameters",{})[k]=v
            option=_trial_config(candidate,config)
            identity=_sha([option,candidate.get("features")])
            if identity not in identities:
                identities.add(identity)
                bucket.append(candidate)
        Random(seed+len(buckets)).shuffle(bucket)
        buckets.append(bucket)
    selected=[]
    for i in range(max(map(len,buckets))):
        for bucket in buckets:
            if i<len(bucket):
                selected.append(bucket[i])
                if len(selected)==budget:
                    return selected
    if len(selected)<2:
        raise ValueError("SEARCH_REQUIRES_TWO_DISTINCT_CANDIDATES")
    return selected


def review_schedule(monitor,*,holdout_start,patience=3,cooldown_dates=10):
    """Chronological degradation review requests, not order/model mutations."""
    if type(patience) is not int or type(cooldown_dates) is not int or not 1<=patience<=252 or not 1<=cooldown_dates<=252:
        raise ValueError("INVALID_RETRAIN_POLICY")
    rows=monitor["daily"]
    baseline=monitor["baseline"]
    streak={}
    last={}
    requests=[]
    for i,row in enumerate(rows):
        if row["date"]<holdout_start:
            continue
        for name,d in sorted(row["models"].items()):
            if name==baseline:
                continue
            streak[name]=streak.get(name,0)+1 if d["drift_alert"] is True else 0
            if streak[name]>=patience and i-last.get(name,-cooldown_dates)>=cooldown_dates:
                requests.append({"date":row["date"],"model":name,"reason":"PERSISTENT_MATURED_ERROR_DRIFT",
                    "recent_mae":d["recent_mae"],"reference_mae":d["reference_mae"],
                    "status":"RETRAIN_REVIEW_REQUIRED"})
                last[name]=i
                streak[name]=0
    return requests


def run(payload,*,config,spaces,holdout_start,budget=12,search_seed=42,selection_metric="mae",
        ensemble=None,regime_ensemble=None,monitoring=None,patience=3,cooldown_dates=10):
    if not isinstance(config,dict):
        raise ValueError("INVALID_AUTONOMY_CONFIG")
    review_schedule({"daily":[],"baseline":"historical_mean"},holdout_start="",patience=patience,cooldown_dates=cooldown_dates)
    if any(x is not None and not isinstance(x,dict) for x in (ensemble,regime_ensemble,monitoring)):
        raise ValueError("INVALID_AUTONOMY_POLICY")
    if monitoring is not None and monitoring.get("baseline","historical_mean")!="historical_mean":
        raise ValueError("AUTONOMY_REQUIRES_HISTORICAL_MEAN_BASELINE")
    validate_panel_metadata(payload,config.get("horizon",5),config.get("sequence_length",1))
    candidates=search_candidates(spaces,config,budget=budget,seed=search_seed)
    selection=tournament(payload,config=config,candidates=candidates,
                         holdout_start=holdout_start,selection_metric=selection_metric,selection_only=True)
    selected=selection["selected_config"]
    winner=selection["selected_model"]
    baseline="historical_mean"
    names=[winner] if winner==baseline else [baseline,winner]
    # Policies are fixed before the holdout, not optimized on its outcomes.
    opts={**selected,"models":names}
    if len(names)>1:
        opts["ensemble"]={"window_dates":20,"minimum_dates":5} if ensemble is None else ensemble
        if regime_ensemble is not None:
            opts["regime_ensemble"]=regime_ensemble
    monitor_config={"window_dates":20,"minimum_dates":5} if monitoring is None else dict(monitoring)
    if monitor_config.get("baseline",baseline)!=baseline:
        raise ValueError("AUTONOMY_REQUIRES_HISTORICAL_MEAN_BASELINE")
    opts["monitoring"]={**monitor_config,"baseline":baseline}
    result=train(project_features(payload,selection["selected_features"]),**opts)
    day=selection["holdout_start"]
    if day not in {f["test_start"] for f in result["folds"]}:
        raise ValueError("HOLDOUT_MUST_START_ON_REFIT_BOUNDARY")
    metrics={name:_metrics(r for r in rows if r["date"]>=day)
             for name,rows in result["predictions"].items()}
    requests=review_schedule(result["monitoring"],holdout_start=day,patience=patience,cooldown_dates=cooldown_dates)
    # Selection and final policy use a single full-panel fitting pass.
    selection.update(holdout_evaluated=True,holdout=metrics[winner],dataset_sha256=result["dataset_sha256"],
                     holdout_mode="selected_model_and_fixed_baseline_walk_forward")
    return {"schema":"atlaspm_synthetic_autonomy_v65","dataset_sha256":result["dataset_sha256"],
            "selection":selection,"holdout_metrics":metrics,"fixed_policy_config":opts,
            "ensemble_weight_history":result["ensemble_weight_history"],
            "regime_weight_history":result["regime_weight_history"],
            "monitoring":result["monitoring"],"retrain_review_requests":requests,
            "scheduled_walk_forward_refits":len(result["folds"]),
            "automatic_retrain_on_drift_enabled":False,"automatic_promotion_enabled":False,
            "status":"SYNTHETIC_RESEARCH_COMPLETE_REVIEW_REQUIRED",**FLAGS}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(**json.loads(args.file.read_text(encoding="utf-8"))),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
