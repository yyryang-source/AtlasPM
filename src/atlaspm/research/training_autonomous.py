"""Bounded autonomous synthetic research; one-use chronological holdout.

Reuses the existing purged trainer and local experiment registry. No model
is approved for real training, promotion or broker execution.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from itertools import groupby
from atlaspm.research.training_engine import train, _fingerprint
from atlaspm.research.training_registry import FLAGS, digest, register, local_path, _connect
from atlaspm.research.training_strategy_metrics import evaluate, paired_lower


def _code_version():
    return digest({str(p.relative_to(Path(__file__).parents[1])):hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(Path(__file__).parents[1].rglob("*.py"))})


def _reserve_holdout(path, start, end, study):
    # The registry is a local integrity boundary, not a tamper-proof authority.
    # Burn the interval before evaluation, including interrupted/failed runs.
    with closing(_connect(path)) as con,con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("""CREATE TABLE IF NOT EXISTS research_holdouts (
            start_date TEXT NOT NULL,end_date TEXT NOT NULL,study TEXT PRIMARY KEY)""")
        if con.execute("SELECT 1 FROM research_holdouts WHERE start_date<=? AND end_date>=? LIMIT 1",
                       (end,start)).fetchone():
            raise ValueError("HOLDOUT_ALREADY_CONSUMED_OR_OVERLAPPING")
        con.execute("INSERT INTO research_holdouts VALUES (?,?,?)",(start,end,study))


def _candidates(features, budget, optional_models):
    profiles=[features]
    for family in (("mom","trend","sma"),("volatility","volume","range"),("ret_1d","reversal","mean_reversion")):
        subset=[f for f in features if any(k in f.lower() for k in family)]
        if subset and subset not in profiles:
            profiles.append(subset)
    configs=[dict(model="ridge",features=features,ridge_alpha=a) for a in (1.,.1,10.)]
    configs.extend(dict(model=m,features=features,ridge_alpha=1.) for m in optional_models)
    configs.extend(dict(model="ridge",features=f,ridge_alpha=1.) for f in profiles[1:])
    return configs[:budget]


def _fit(payload, candidate, settings):
    features=candidate["features"]
    if features!=payload["feature_names"]:
        payload={**payload,"feature_names":features,"sessions":[
            {**s,"assets":[{**a,"features":{f:a["features"][f] for f in features}}
                            for a in s["assets"]]} for s in payload["sessions"]]}
    model=candidate["model"]
    options={**settings,"models":[model],"ridge_alpha":candidate["ridge_alpha"]}
    if model in ("ridge","historical_mean"):
        options["incremental_ridge"]=True
    result=train(payload,**options)
    return result["predictions"][model],result["dependency_versions"]


def _baselines(rows, sessions, momentum):
    # Momentum field must be explicitly defined by the caller's synthetic schema.
    values={(s["date"],a["security_id"]):a["features"][momentum]
            for s in sessions for a in s["assets"]}
    return {"equal_weight":[{**r,"prediction":0.} for r in rows],
            "momentum":[{**r,"prediction":values[r["date"],r["security_id"]]} for r in rows]}


def _identity(rows):
    return digest([(r["date"],r["security_id"],r["label_end_date"],r["actual"]) for r in rows])


def _strategy_id(result):
    return digest([(r["signal_date"],r["selected"]) for r in result["daily"]])


def research(payload, *, registry_path, holdout_start, momentum_feature,
             budget=6, seed=42, minimum_train_sessions=20, test_sessions=5,
             embargo_sessions=1, cost_bps=10., top_fraction=.25,
             minimum_validation_periods=20, bootstrap_draws=2000,
             bootstrap_block=5, optional_models=()):
    if payload.get("mode")!="synthetic" or payload.get("synthetic_fixture") is not True:
        raise ValueError("REAL_TRAINING_DATA_NOT_APPROVED")
    if any(k in payload and payload[k] is not v for k,v in FLAGS.items()):
        raise ValueError("RESEARCH_PERMISSION_OVERRIDE_DENIED")
    if type(budget) is not int or not 1<=budget<=8 or type(seed) is not int or not 0<=seed<2**31:
        raise ValueError("INVALID_AUTONOMOUS_BUDGET_OR_SEED")
    if not isinstance(optional_models,(tuple,list)) or len(set(optional_models))!=len(optional_models) or set(optional_models)-{"elastic_net","random_forest","lightgbm","xgboost","catboost","mlp"}:
        raise ValueError("INVALID_OPTIONAL_RESEARCH_MODELS")
    if type(minimum_validation_periods) is not int or minimum_validation_periods<20:
        raise ValueError("INSUFFICIENT_MINIMUM_VALIDATION_PERIODS")
    if type(bootstrap_draws) is not int or not 1000<=bootstrap_draws<=10000:
        raise ValueError("INVALID_BOOTSTRAP_DRAWS")
    if type(bootstrap_block) is not int or not 2<=bootstrap_block<=minimum_validation_periods//2:
        raise ValueError("INVALID_BOOTSTRAP_BLOCK")
    for n,lo in ((minimum_train_sessions,1),(test_sessions,1),(embargo_sessions,0)):
        if type(n) is not int or n<lo:
            raise ValueError("INVALID_FOLD_CONFIG")
    features=payload["feature_names"]
    if momentum_feature not in features:
        raise ValueError("MOMENTUM_BASELINE_FEATURE_REQUIRED")
    sessions=payload["sessions"]
    first=minimum_train_sessions+1+embargo_sessions
    if type(holdout_start) is not int or holdout_start-first<minimum_validation_periods+2 or (holdout_start-first)%test_sessions or len(sessions)-holdout_start<minimum_validation_periods+2:
        raise ValueError("INVALID_OR_INSUFFICIENT_FROZEN_HOLDOUT")
    path=local_path(registry_path)
    prefix={**payload,"sessions":sessions[:holdout_start]}
    settings=dict(horizon=1,minimum_train_sessions=minimum_train_sessions,
                  test_sessions=test_sessions,embargo_sessions=embargo_sessions,seed=seed)
    evaluation=dict(top_fraction=top_fraction,cost_bps=cost_bps)
    candidates=_candidates(features,budget,optional_models)
    baseline_config=dict(model="historical_mean",features=features,ridge_alpha=1.)
    rows,versions=_fit(prefix,baseline_config,settings)
    identity=_identity(rows)
    baselines={m:evaluate(r,prefix["sessions"],**evaluation)
               for m,r in _baselines(rows,prefix["sessions"],momentum_feature).items()}
    if min(b["metrics"]["periods"] for b in baselines.values())<minimum_validation_periods:
        raise ValueError("INSUFFICIENT_VALIDATION_PERIODS")
    seen={_strategy_id(b):m for m,b in baselines.items()}
    trials=[]
    # Two fixed benchmark comparisons per planned candidate, including failures.
    tail=.05/(2*len(candidates))
    for candidate in candidates:
        trial=dict(config=candidate,seed=seed,status="REJECTED",reasons=[])
        try:
            forecasts,deps=_fit(prefix,candidate,settings)
            if _identity(forecasts)!=identity:
                raise ValueError("CANDIDATE_UNIVERSE_MISMATCH")
            outcome=evaluate(forecasts,prefix["sessions"],**evaluation)
            strategy_id=_strategy_id(outcome)
            trial.update(metrics=outcome["metrics"],strategy_id=strategy_id,dependencies=deps)
            if strategy_id in seen:
                trial["reasons"].append("DUPLICATE_ALLOCATION:"+seen[strategy_id])
            else:
                seen[strategy_id]=digest(candidate)
                evidence={m:paired_lower(outcome,b,seed=seed,draws=bootstrap_draws,
                                         block=bootstrap_block,tail=tail) for m,b in baselines.items()}
                trial["comparison"]=evidence
                ric=outcome["metrics"]["mean_daily_rank_ic"]
                if ric is None or ric<=0:
                    trial["reasons"].append("NONPOSITIVE_RANK_IC")
                if any(x["lower_bound"]<=0 for x in evidence.values()):
                    trial["reasons"].append("NO_MULTIPLICITY_ADJUSTED_NET_ADVANTAGE")
                if not trial["reasons"]:
                    trial["status"]="CHALLENGER_RESEARCH_ONLY"
        except (ValueError,ImportError,ArithmeticError) as exc:
            trial["reasons"].append(str(exc))
        trials.append(trial)
    eligible=[t for t in trials if t["status"]=="CHALLENGER_RESEARCH_ONLY"]
    selected=max(eligible,key=lambda t:(min(x["lower_bound"] for x in t["comparison"].values()),
                                        digest(t["config"]))) if eligible else None
    config=dict(settings=settings,evaluation=evaluation,candidates=candidates,seed=seed,
                code_sha256=_code_version(),holdout_start=sessions[holdout_start]["date"],
                holdout_end=sessions[-1]["date"],momentum_feature=momentum_feature,
                minimum_validation_periods=minimum_validation_periods,
                bootstrap_draws=bootstrap_draws,bootstrap_block=bootstrap_block)
    validation=dict(dataset_sha256=_fingerprint(prefix),trials=trials,
                    baselines={m:b["metrics"] for m,b in baselines.items()},
                    selected=selected["config"] if selected else "equal_weight",
                    baseline_dependencies=versions,**FLAGS)
    validation_id=register(path,config,validation,kind="experiment")
    _reserve_holdout(path,config["holdout_start"],config["holdout_end"],validation_id)
    # Only the selected candidate and fixed references enter the holdout.
    # Refitting is allowed on strictly matured historical labels, never retuning.
    reference,dependencies=_fit(payload,baseline_config,settings)
    holdout=[r for r in reference if r["date"]>=config["holdout_start"]]
    fixed=_baselines(holdout,sessions,momentum_feature)
    if selected:
        forecasts,deps=_fit(payload,selected["config"],settings)
        forecasts=[r for r in forecasts if r["date"]>=config["holdout_start"]]
        if _identity(forecasts)!=_identity(holdout):
            raise ValueError("HOLDOUT_UNIVERSE_MISMATCH")
        fixed["selected"]=forecasts
        dependencies.update(deps)
    final={m:evaluate(r,sessions,**evaluation) for m,r in fixed.items()}
    holdout_comparison={m:paired_lower(final["selected"],final[m],seed=seed,
                        draws=bootstrap_draws,block=bootstrap_block,tail=.025)
                        for m in ("equal_weight","momentum")} if selected else {}
    passed=bool(selected and all(v["lower_bound"]>0 for v in holdout_comparison.values())
                and (final["selected"]["metrics"]["mean_daily_rank_ic"] or 0)>0)
    report=dict(dataset_sha256=_fingerprint(payload),validation_run_id=validation_id,
                validation=validation,holdout={m:v["metrics"] for m,v in final.items()},
                holdout_comparison=holdout_comparison,holdout_dependencies=dependencies,
                selected_research_strategy=selected["config"] if passed else "equal_weight",
                status="CHALLENGER_HOLDOUT_PASS_RESEARCH_ONLY" if passed else "BASELINE_FALLBACK",
                holdout_consumed=True,holdout_mode="frozen_selection_purged_refit",
                automatic_promotion_enabled=False,capacity_verified=False,**FLAGS)
    run_id=register(path,config,report,kind="experiment")
    return {**report,"run_id":run_id}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(research(**json.loads(args.file.read_text(encoding="utf-8"))),
                     indent=2,allow_nan=False))


if __name__=="__main__":
    main()
