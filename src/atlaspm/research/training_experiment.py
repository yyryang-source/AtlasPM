"""Bounded synthetic model experiment, with a separate chronological holdout."""
from __future__ import annotations
from itertools import product
from math import prod
import argparse
import json
import hashlib
from pathlib import Path
from atlaspm.research.training_engine import train, _metrics
from atlaspm.research.training_dataset import HORIZONS

TUNABLE = {"ridge_alpha", "sequence_epochs", "seed"}


def experiment(payload, *, config, grid, holdout_start, selection_model="ridge"):
    if payload.get("mode")!="synthetic" or payload.get("synthetic_fixture") is not True:
        raise ValueError("REAL_TRAINING_DATA_NOT_APPROVED")
    if not isinstance(config,dict) or not isinstance(grid,dict) or not grid or set(grid)-TUNABLE:
        raise ValueError("INVALID_EXPERIMENT_GRID")
    if any(not isinstance(v,list) or not v for v in grid.values()):
        raise ValueError("INVALID_EXPERIMENT_GRID_VALUES")
    if prod(len(v) for v in grid.values())>32:
        raise ValueError("EXPERIMENT_TRIAL_LIMIT")
    if config.get("model_artifact_path") is not None:
        raise ValueError("EXPERIMENT_ARTIFACTS_REQUIRE_SELECTED_RUN")
    if config.get("prediction_archive_path") is not None:
        raise ValueError("EXPERIMENT_REQUIRES_MEMORY_PREDICTIONS")
    sessions=payload["sessions"]
    horizon=config.get("horizon",5)
    if type(horizon) is not int or horizon not in HORIZONS:
        raise ValueError("INVALID_LABEL_HORIZON")
    if type(holdout_start) is not int or not 1<holdout_start<len(sessions)-horizon:
        raise ValueError("INVALID_HOLDOUT_SPLIT")
    minimum=config.get("minimum_train_sessions",20)
    test=config.get("test_sessions",5)
    embargo=config.get("embargo_sessions",1)
    length=config.get("sequence_length",1)
    if any(type(v) is not int or v<lo for v,lo in ((minimum,1),(test,1),(embargo,0),(length,1))):
        raise ValueError("INVALID_FOLD_CONFIG")
    first=minimum+horizon+embargo+length-1
    if holdout_start<=first+horizon or (holdout_start-first)%test:
        raise ValueError("HOLDOUT_MUST_START_ON_REFIT_BOUNDARY")
    # Never send holdout prices or labels to the tuning trials.
    # train treats the input as read-only; slice references rather than copying every row.
    prefix={**payload,"sessions":sessions[:holdout_start]}
    keys=sorted(grid)
    trials=[]
    reference=None
    for values in product(*(grid[k] for k in keys)):
        params=dict(zip(keys,values))
        result=train(prefix,**{**config,**params})
        if selection_model not in result["predictions"]:
            raise ValueError("SELECTION_MODEL_NOT_AVAILABLE")
        rows=result["predictions"][selection_model]
        identity_hash=hashlib.sha256()
        for row in rows:
            identity_hash.update(json.dumps([row["security_id"],row["date"],row["label_end_date"],row["actual"]],separators=(",",":"),allow_nan=False).encode())
            identity_hash.update(b"\n")
        identity=identity_hash.hexdigest()
        if reference is None:
            reference=identity
        elif identity!=reference:
            raise ValueError("EXPERIMENT_TRIAL_UNIVERSE_MISMATCH")
        stats=result["metrics"][selection_model]
        if stats["mae"] is None:
            raise ValueError("NO_MATURED_VALIDATION_LABELS")
        trials.append(dict(parameters=params,validation=stats))
        del rows,result
    best=min(trials,key=lambda t:t["validation"]["mae"])
    selected={**config,**best["parameters"]}
    # Align the holdout to an actual model refit boundary; otherwise the
    # validation run's truncated last fold could obscure the evaluation split.
    full=train(payload,**selected)
    day=sessions[holdout_start]["date"]
    if day not in {f["test_start"] for f in full["folds"]}:
        raise ValueError("HOLDOUT_MUST_START_ON_REFIT_BOUNDARY")
    rows=[r for r in full["predictions"][selection_model] if r["date"]>=day]
    if not rows:
        raise ValueError("NO_HOLDOUT_PREDICTIONS")
    fingerprint=hashlib.sha256(json.dumps({"config":config,"grid":grid,"holdout_start":holdout_start,
                    "selection_model":selection_model},sort_keys=True,allow_nan=False).encode()).hexdigest()
    return dict(experiment_sha256=fingerprint,dataset_sha256=full["dataset_sha256"],
                selection_rule="minimum_validation_mae",selection_model=selection_model,
                validation_end=sessions[holdout_start-1]["date"],holdout_start=day,
                trials=trials,selected_parameters=best["parameters"],
                selected_validation=best["validation"],holdout=_metrics(rows),
                holdout_predictions=rows,holdout_mode="walk_forward_refit_matured_past_labels",
                research_only=True,approved_for_model_training=False,
                historical_security_identity_verified=False,model_registry_admission=False,
                live_execution_enabled=False,broker_order_submission_enabled=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",required=True,type=Path)
    args=parser.parse_args()
    import sys
    json.dump(experiment(**json.loads(args.file.read_text(encoding="utf-8"))),sys.stdout,indent=2,allow_nan=False)
    print()


if __name__=="__main__":
    main()
