"""Predeclared window/cadence competition with matured-only regime weights.

A separate synthetic research experiment, not a qualified paper challenger.
No model is automatically promoted and no real trading/data permissions change.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from atlaspm.engine.contracts import finite
from atlaspm.research.training_autonomous import _code_version, _identity, _reserve_holdout, _baselines
from atlaspm.research.training_dataset import MarketRegimes, validated_sessions, validate_panel_metadata
from atlaspm.research.training_engine import train, _fingerprint
from atlaspm.research.training_ensemble import iter_ensemble
from atlaspm.research.training_registry import FLAGS, digest, local_path, register
from atlaspm.research.training_sequence import SEQUENCE_MODELS
from atlaspm.research.training_strategy_metrics import evaluate, paired_lower


def sequence_resources(*, device="auto", batch_size=64, sequence_length=1,
                       feature_count=1, batch_input_budget_mib=16, gpu_calibrated=False):
    """Bound input tensor bytes, not total VRAM. GPU requires explicit evidence/opt-in.

    The small RX 7600 benchmark favored CPU. Do not extrapolate that result
    to other shapes. An uncalibrated auto setting stays on CPU.
    """
    if device not in ("auto","cpu","cuda") or type(gpu_calibrated) is not bool:
        raise ValueError("INVALID_SEQUENCE_RESOURCE_DEVICE")
    for value,lo,hi in ((batch_size,1,8192),(sequence_length,1,252),
                        (feature_count,1,100000),(batch_input_budget_mib,1,1024)):
        if type(value) is not int or not lo<=value<=hi:
            raise ValueError("INVALID_SEQUENCE_RESOURCE_BUDGET")
    per_sample=4*sequence_length*feature_count
    cap=batch_input_budget_mib*1024**2//per_sample
    if cap<1:
        raise ValueError("ONE_SEQUENCE_EXCEEDS_INPUT_BUDGET")
    chosen="cpu"
    if device=="cuda" or (device=="auto" and gpu_calibrated):
        try:
            import torch
            available=torch.cuda.is_available()
        except ImportError:
            available=False
        if device=="cuda" and not available:
            raise ValueError("REQUESTED_SEQUENCE_DEVICE_UNAVAILABLE")
        chosen="cuda" if available else "cpu"
    batch=min(batch_size,cap)
    return dict(device=chosen,requested_device=device,batch_size=batch,
                requested_batch_size=batch_size,batch_input_budget_mib=batch_input_budget_mib,
                maximum_float32_input_bytes=batch*per_sample,
                gpu_calibrated_by_operator=gpu_calibrated,
                total_vram_budget_guaranteed=False,mixed_precision=False)


def _profiles(minimum, supplied):
    raw=supplied if supplied is not None else [
        dict(name="ridge_short",model="ridge",window=minimum,cadence=5,alpha=1.),
        dict(name="ridge_medium",model="ridge",window=3*minimum,cadence=5,alpha=1.),
        dict(name="ridge_slow",model="ridge",window=3*minimum,cadence=10,alpha=1.)]
    if not isinstance(raw,list) or not 1<=len(raw)<=5:
        raise ValueError("INVALID_ADAPTIVE_PROFILE_COUNT")
    out=[dict(name="ridge_reference",model="ridge",window=None,cadence=5,alpha=1.)]
    for p in raw:
        if not isinstance(p,dict) or set(p)!={"name","model","window","cadence","alpha"}:
            raise ValueError("INVALID_ADAPTIVE_PROFILE_SCHEMA")
        name=p["name"]
        if not isinstance(name,str) or not name.isidentifier() or name in {"historical_mean","adaptive","equal_weight","momentum"} or any(name==v["name"] for v in out):
            raise ValueError("INVALID_ADAPTIVE_PROFILE_NAME")
        if p["model"] not in ("ridge",)+SEQUENCE_MODELS:
            raise ValueError("INVALID_ADAPTIVE_PROFILE_MODEL")
        if p["window"] is not None and (type(p["window"]) is not int or not minimum<=p["window"]<=5000):
            raise ValueError("INVALID_ADAPTIVE_PROFILE_WINDOW")
        if type(p["cadence"]) is not int or not 1<=p["cadence"]<=30:
            raise ValueError("INVALID_ADAPTIVE_PROFILE_CADENCE")
        alpha=finite(p["alpha"],"alpha",minimum=.000001)
        out.append({**p,"alpha":alpha})
    return out


def adaptive_research(payload, *, registry_path, holdout_start, momentum_feature,
                      profiles=None, minimum_train_sessions=20, sequence_length=1,
                      sequence_epochs=3, embargo_sessions=1, seed=42,
                      window_dates=20, minimum_dates=10, temperature=.001,
                      drift_ratio=1.5, minimum_improvement=.02,
                      device="auto", batch_size=64, batch_input_budget_mib=16,
                      gpu_calibrated=False, cost_bps=10.,top_fraction=.25,
                      bootstrap_draws=1000, bootstrap_block=5, execution_aligned_labels=False):
    if type(execution_aligned_labels) is not bool:
        raise ValueError('INVALID_EXECUTION_ALIGNED_MODE')
    if any(k in payload and payload[k] is not v for k,v in FLAGS.items()):
        raise ValueError("RESEARCH_PERMISSION_OVERRIDE_DENIED")
    features=validate_panel_metadata(payload,1,sequence_length)
    if momentum_feature not in features:
        raise ValueError("MOMENTUM_BASELINE_FEATURE_REQUIRED")
    for value,lo,hi in ((minimum_train_sessions,5,1000),(sequence_epochs,1,200),
                        (embargo_sessions,0,252),(seed,0,2**31-1),
                        (window_dates,2,252),(minimum_dates,2,252),
                        (bootstrap_draws,1000,10000),(bootstrap_block,2,10)):
        if type(value) is not int or not lo<=value<=hi:
            raise ValueError("INVALID_ADAPTIVE_SETTINGS")
    if minimum_dates>window_dates:
        raise ValueError("INVALID_ADAPTIVE_SETTINGS")
    candidates=_profiles(minimum_train_sessions,profiles)
    if sequence_length<2 and any(p["model"] in SEQUENCE_MODELS for p in candidates):
        raise ValueError("SEQUENCE_MODELS_REQUIRE_MULTIPLE_SESSIONS")
    resources=sequence_resources(device=device,batch_size=batch_size,
        sequence_length=sequence_length,feature_count=len(features),
        batch_input_budget_mib=batch_input_budget_mib,gpu_calibrated=gpu_calibrated)
    sessions=payload["sessions"]
    first=minimum_train_sessions+1+int(execution_aligned_labels)+embargo_sessions+sequence_length-1
    if type(holdout_start) is not int or holdout_start-first<22 or len(sessions)-holdout_start<22:
        raise ValueError("INSUFFICIENT_ADAPTIVE_HOLDOUT")
    tracker=MarketRegimes()
    for day,marks in validated_sessions(sessions,features):
        tracker.observe(day,marks)
    prefix={**payload,"sessions":sessions[:holdout_start]}
    config=dict(profiles=candidates,minimum_train_sessions=minimum_train_sessions,
                sequence_length=sequence_length,sequence_epochs=sequence_epochs,
                embargo_sessions=embargo_sessions,seed=seed,window_dates=window_dates,
                minimum_dates=minimum_dates,temperature=temperature,drift_ratio=drift_ratio,
                minimum_improvement=minimum_improvement,resources=resources,
                momentum_feature=momentum_feature,cost_bps=cost_bps,top_fraction=top_fraction,
                bootstrap_draws=bootstrap_draws,bootstrap_block=bootstrap_block,
                execution_aligned_labels=execution_aligned_labels,
                holdout_start=sessions[holdout_start]["date"],holdout_end=sessions[-1]["date"],
                code_sha256=_code_version())
    path=local_path(registry_path)
    _reserve_holdout(path,sessions[0]["date"],config["holdout_start"],config["holdout_end"],
                     digest({"config":config,"prefix":_fingerprint(prefix)}))
    streams={}
    audits={}
    settings=dict(horizon=1,entry_lag_sessions=int(execution_aligned_labels),minimum_train_sessions=minimum_train_sessions,
                  sequence_length=sequence_length,sequence_epochs=sequence_epochs,
                  embargo_sessions=embargo_sessions,seed=seed)
    for p in candidates:
        models=[p["model"]]
        if p["name"]=="ridge_reference":
            models.append("historical_mean")
        result=train(payload,**settings,models=models,test_sessions=p["cadence"],
                     training_window_sessions=p["window"],ridge_alpha=p["alpha"],
                     device=resources["device"] if p["model"] in SEQUENCE_MODELS else "cpu",
                     batch_size=resources["batch_size"],
                     incremental_ridge=p["model"]=="ridge" and p["window"] is None)
        streams[p["name"]]=result["predictions"][p["model"]]
        if p["name"]=="ridge_reference":
            baseline=result["predictions"]["historical_mean"]
        audits[p["name"]]=dict(fold_count=len(result["folds"]),
            dependency_versions=result["dependency_versions"],
            training_rows_total=sum(f["training_rows"] for f in result["folds"]))
    reference=streams["ridge_reference"]
    identity=_identity(reference)
    if any(_identity(rows)!=identity for rows in streams.values()) or _identity(baseline)!=identity:
        raise ValueError("ADAPTIVE_PROFILE_ALIGNMENT_MISMATCH")
    combined=[]
    weights=[]
    for rows,audit in iter_ensemble(streams,window_dates=window_dates,minimum_dates=minimum_dates,
        temperature=temperature,regimes=tracker.states,reference_model="ridge_reference",
        drift_ratio=drift_ratio,minimum_improvement=minimum_improvement):
        combined.extend(rows)
        weights.append(audit)
    streams["adaptive"]=combined
    streams["historical_mean"]=baseline
    streams.update(_baselines(reference,sessions,momentum_feature))
    boundary=config["holdout_start"]
    validation={m:evaluate([r for r in rows if r["label_end_date"]<boundary],prefix["sessions"],
                    top_fraction=top_fraction,cost_bps=cost_bps,execution_aligned_labels=execution_aligned_labels)["metrics"] for m,rows in streams.items()}
    holdout={m:evaluate([r for r in rows if r["date"]>=boundary],sessions,
                    top_fraction=top_fraction,cost_bps=cost_bps,execution_aligned_labels=execution_aligned_labels) for m,rows in streams.items()}
    comparisons={m:paired_lower(holdout["adaptive"],holdout[m],seed=seed,
                  draws=bootstrap_draws,block=bootstrap_block,tail=.05/3)
                 for m in ("ridge_reference","equal_weight","momentum")}
    passed=all(v["lower_bound"]>0 for v in comparisons.values()) and (holdout["adaptive"]["metrics"]["mean_daily_rank_ic"] or 0)>0
    report=dict(status="ADAPTIVE_RESEARCH_PASS_REVIEW_REQUIRED" if passed else "ADAPTIVE_ADVANTAGE_NOT_ESTABLISHED",
        dataset_sha256=_fingerprint(payload),validation=validation,
        holdout={m:v["metrics"] for m,v in holdout.items()},holdout_comparison=comparisons,
        weight_history=weights,profile_audit=audits,resource_plan=resources,
        holdout_consumed=True,holdout_mode="predeclared_policy_matured_only",
        automatic_promotion_enabled=False,automatic_retirement=False,
        capacity_verified=False,**FLAGS)
    run_id=register(path,config,report,kind="experiment")
    return {**report,"run_id":run_id}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file",type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(adaptive_research(**json.loads(args.file.read_text(encoding="utf-8"))),
                     indent=2,allow_nan=False))


if __name__=="__main__":
    main()
