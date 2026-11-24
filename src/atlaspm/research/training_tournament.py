"""V6.2 bounded synthetic model tournament using the existing fold engine.

Compare registered estimators on identical pre-holdout predictions; select
one configuration and evaluate only that configuration on the holdout.
No real training, registry admission or broker permission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from math import isfinite
from pathlib import Path

from atlaspm.research.training_dataset import HORIZONS, validate_panel_metadata
from atlaspm.research.training_engine import train, _metrics
from atlaspm.research.training_models import MODELS, validate_parameters
from atlaspm.research.training_sequence import SEQUENCE_MODELS

METRICS={"mae":1,"rmse":1,"mean_daily_rank_ic":-1,"mean_daily_ic":-1}
COMMON={"horizon","minimum_train_sessions","test_sessions","embargo_sessions",
        "sequence_length","seed","device","batch_size","array_budget_mib",
        "mixed_precision","sequence_epochs","ridge_alpha"}
CAP=32


def _sha(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _identity(rows):
    digest=hashlib.sha256()
    for r in rows:
        digest.update(json.dumps([r["security_id"],r["date"],r["label_end_date"],r["actual"]],
                                 separators=(",",":"),allow_nan=False).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _trial_config(candidate,config):
    if not isinstance(candidate,dict) or set(candidate)-{"model","ridge_alpha","sequence_epochs","parameters","features"}:
        raise ValueError("INVALID_TOURNAMENT_CANDIDATE")
    name=candidate.get("model")
    if name not in MODELS+SEQUENCE_MODELS:
        raise ValueError("UNKNOWN_TOURNAMENT_MODEL")
    if "ridge_alpha" in candidate and name!="ridge":
        raise ValueError("RIDGE_PARAMETER_ON_WRONG_MODEL")
    if "sequence_epochs" in candidate and name not in SEQUENCE_MODELS:
        raise ValueError("SEQUENCE_PARAMETER_ON_WRONG_MODEL")
    alpha=candidate.get("ridge_alpha",config.get("ridge_alpha",1.))
    epochs=candidate.get("sequence_epochs",config.get("sequence_epochs",12))
    if type(alpha) not in (int,float) or not isfinite(alpha) or alpha<.000001:
        raise ValueError("INVALID_TOURNAMENT_RIDGE_ALPHA")
    if type(epochs) is not int or not 1<=epochs<=200:
        raise ValueError("INVALID_TOURNAMENT_EPOCHS")
    if name in SEQUENCE_MODELS and config.get("sequence_length",1)<2:
        raise ValueError("SEQUENCE_MODELS_REQUIRE_MULTIPLE_SESSIONS")
    params=validate_parameters(name,candidate.get("parameters",{}))
    result={**config,"models":[name],"ridge_alpha":alpha,"sequence_epochs":epochs}
    if params:
        result["model_parameters"]={name:params}
    return result


def project_features(payload, names):
    original=payload["feature_names"]
    if not isinstance(names,list) or not names or len(set(names))!=len(names) or any(n not in original for n in names):
        raise ValueError("INVALID_FEATURE_SUBSET")
    if names==original:
        return payload
    return {**payload,"feature_names":names,"sessions":[
        {**session,"assets":[{**asset,"features":{n:asset["features"][n] for n in names}}
                            for asset in session["assets"]]} for session in payload["sessions"]]}


def tournament(payload,*,config,candidates,holdout_start,selection_metric="mae",selection_only=False):
    if type(selection_only) is not bool:
        raise ValueError("INVALID_SELECTION_ONLY")
    if not isinstance(config,dict) or set(config)-COMMON:
        raise ValueError("INVALID_TOURNAMENT_CONFIG")
    if not isinstance(candidates,list) or not 2<=len(candidates)<=CAP:
        raise ValueError("TOURNAMENT_REQUIRES_2_TO_32_CANDIDATES")
    if selection_metric not in METRICS:
        raise ValueError("INVALID_TOURNAMENT_METRIC")
    horizon=config.get("horizon",5)
    length=config.get("sequence_length",1)
    validate_panel_metadata(payload,horizon,length)
    if horizon not in HORIZONS:
        raise ValueError("INVALID_LABEL_HORIZON")
    minimum=config.get("minimum_train_sessions",20)
    step=config.get("test_sessions",5)
    embargo=config.get("embargo_sessions",1)
    if any(type(v) is not int or v<lo for v,lo in ((minimum,1),(step,1),(embargo,0))):
        raise ValueError("INVALID_FOLD_CONFIG")
    sessions=payload["sessions"]
    first=minimum+horizon+embargo+length-1
    if (type(holdout_start) is not int or not first+horizon<holdout_start<len(sessions)-horizon
            or (holdout_start-first)%step):
        raise ValueError("HOLDOUT_MUST_START_ON_REFIT_BOUNDARY")
    options=[_trial_config(c,config) for c in candidates]
    subsets=[c.get("features",payload["feature_names"]) for c in candidates]
    for names in subsets:
        if not isinstance(names,list) or not names or any(not isinstance(n,str) for n in names) or len(set(names))!=len(names) or any(n not in payload["feature_names"] for n in names):
            raise ValueError("INVALID_FEATURE_SUBSET")
    if len({_sha([c,names]) for c,names in zip(options,subsets)})!=len(options):
        raise ValueError("DUPLICATE_TOURNAMENT_CANDIDATE")
    # Holdout data never enters candidate selection. Train one candidate at
    # a time; discard its prediction arrays after scoring.
    prefix={**payload,"sessions":sessions[:holdout_start]}
    reference=None
    trials=[]
    for index,option in enumerate(options):
        name=option["models"][0]
        result=train(project_features(prefix,subsets[index]),**option)
        rows=result["predictions"][name]
        identity=_identity(rows)
        if reference is None:
            reference=identity
        elif reference!=identity:
            raise ValueError("TOURNAMENT_VALIDATION_UNIVERSE_MISMATCH")
        stats=result["metrics"][name]
        score=stats[selection_metric]
        if score is not None and not isfinite(score):
            raise ValueError("NONFINITE_TOURNAMENT_SCORE")
        trials.append({"index":index,"candidate":candidates[index],"validation":stats,
                       "selection_score":score,"dependency_versions":result["dependency_versions"]})
        del result,rows
    eligible=[t for t in trials if t["selection_score"] is not None]
    if not eligible:
        raise ValueError("NO_VALID_TOURNAMENT_SCORE")
    # Deterministic tie break by caller-specified candidate order.
    best=min(eligible,key=lambda t:(METRICS[selection_metric]*t["selection_score"],t["index"]))
    selected=options[best["index"]]
    winner=selected["models"][0]
    selected_features=subsets[best["index"]]
    day=sessions[holdout_start]["date"]
    report={"schema":"atlaspm_synthetic_model_tournament_v62",
            "experiment_sha256":_sha({"config":config,"candidates":candidates,
                                      "holdout_start":holdout_start,"selection_metric":selection_metric}),
            "validation_identity_sha256":reference,
            "selection_metric":selection_metric,"trial_count":len(trials),"trials":trials,
            "selected_model":winner,"selected_config":selected,"selected_features":selected_features,"selected_trial_index":best["index"],
            "validation_end":sessions[holdout_start-1]["date"],"holdout_start":day,
            "research_only":True,"approved_for_model_training":False,
            "historical_security_identity_verified":False,"model_registry_admission":False,
            "live_execution_enabled":False,"broker_order_submission_enabled":False}
    if selection_only:
        return {**report,"holdout_evaluated":False}
    final=train(project_features(payload,selected_features),**selected)
    if day not in {f["test_start"] for f in final["folds"]}:
        raise ValueError("HOLDOUT_MUST_START_ON_REFIT_BOUNDARY")
    holdout=[r for r in final["predictions"][winner] if r["date"]>=day]
    if not holdout:
        raise ValueError("NO_HOLDOUT_PREDICTIONS")
    return {**report,"dataset_sha256":final["dataset_sha256"],
            "holdout":_metrics(holdout),"holdout_predictions":holdout,"holdout_evaluated":True,
            "holdout_mode":"winner_only_walk_forward_refits_on_matured_past_labels"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(tournament(**json.loads(args.file.read_text(encoding="utf-8"))),
                     indent=2,allow_nan=False))


if __name__=="__main__":
    main()
