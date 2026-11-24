"""Synthetic expanding-window return prediction, research only."""
from __future__ import annotations
import argparse
import hashlib
import json
from math import sqrt
from pathlib import Path
from statistics import fmean
from atlaspm.research.training_dataset import build_panel, purged_folds
from atlaspm.engine.contracts import finite
from atlaspm.research.training_models import MODELS, versions, fit_predict


def _correlation(a,b):
    ma,mb=fmean(a),fmean(b)
    aa=sum((x-ma)**2 for x in a)
    bb=sum((y-mb)**2 for y in b)
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/sqrt(aa*bb) if aa and bb else None


def _ranks(values):
    order=sorted(range(len(values)),key=lambda i:values[i])
    ranks=[0.]*len(values)
    i=0
    while i<len(order):
        j=i+1
        while j<len(order) and values[order[j]]==values[order[i]]:
            j+=1
        for k in order[i:j]:
            ranks[k]=(i+j-1)/2
        i=j
    return ranks


def _metrics(rows):
    dates={}
    for row in rows:
        dates.setdefault(row["date"],[]).append(row)
    ics,rank_ics=[],[]
    for group in dates.values():
        p=[r["prediction"] for r in group]
        y=[r["actual"] for r in group]
        ic=_correlation(p,y)
        ric=_correlation(_ranks(p),_ranks(y))
        if ic is not None:
            ics.append(ic)
        if ric is not None:
            rank_ics.append(ric)
    return dict(samples=len(rows),dates=len(dates),
                mae=fmean(abs(r["prediction"]-r["actual"]) for r in rows),
                rmse=sqrt(fmean((r["prediction"]-r["actual"])**2 for r in rows)),
                mean_daily_ic=fmean(ics) if ics else None,
                mean_daily_rank_ic=fmean(rank_ics) if rank_ics else None,
                valid_ic_dates=len(ics),valid_rank_ic_dates=len(rank_ics))


def train(payload, *, horizon=5, minimum_train_sessions=20, test_sessions=5,
          embargo_sessions=1, ridge_alpha=1., models=None, seed=42):
    models = ['ridge','historical_mean'] if models is None else models
    if not isinstance(models,list) or not models or any(m not in MODELS for m in models) or len(set(models))!=len(models):
        raise ValueError('INVALID_MODEL_SELECTION')
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise ValueError('INVALID_MODEL_SEED')
    alpha=finite(ridge_alpha,"ridge_alpha",minimum=.000001)
    panel=build_panel(payload,horizon)
    folds=purged_folds(panel,minimum_train_sessions=minimum_train_sessions,
                       test_sessions=test_sessions,embargo_sessions=embargo_sessions)
    try:
        import numpy as np
    except ImportError as exc:
        raise ValueError("TRAINING_REQUIRES_PORTFOLIO_EXTRA") from exc
    samples=panel["samples"]
    dependency_versions=versions(models)
    predictions={m:[] for m in models}
    model_settings={}
    audit=[]
    for fold in folds:
        train_rows=[samples[i] for i in fold["train"]]
        test_rows=[samples[i] for i in fold["test"]]
        x=np.asarray([r["x"] for r in train_rows],dtype=float)
        y=np.asarray([r["y"] for r in train_rows],dtype=float)
        mean=x.mean(axis=0)
        scale=x.std(axis=0)
        scale=np.where(scale>1e-12,scale,1.)
        z=(x-mean)/scale
        intercept=float(y.mean())
        coef=np.linalg.solve(z.T@z+alpha*np.eye(z.shape[1]),z.T@(y-intercept))
        test=np.asarray([r["x"] for r in test_rows],dtype=float)
        ridge=((test-mean)/scale)@coef+intercept
        for name in models:
            if name=="ridge":
                values=ridge
                settings={"alpha":alpha}
            elif name=="historical_mean":
                values=np.full(len(test_rows),intercept)
                settings={}
            else:
                values,settings=fit_predict(name,z,y,(test-mean)/scale,seed)
            model_settings[name]=settings
            for row,value in zip(test_rows,values):
                base={k:row[k] for k in ("security_id","date","label_end_date")}
                base["actual"]=row["y"]
                predictions[name].append(dict(base,prediction=finite(float(value),"prediction")))
        audit.append(dict(test_start=panel["dates"][fold["test_start_index"]],
                          test_end=panel["dates"][fold["test_end_index"]],
                          latest_train_label=panel["dates"][fold["latest_training_label_end"]],
                          training_rows=len(train_rows),test_rows=len(test_rows),
                          feature_mean=mean.tolist(),feature_scale=scale.tolist(),
                          coefficients=coef.tolist(),intercept=intercept))
    fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    return dict(version="synthetic_return_research_v2",dataset_sha256=fingerprint,
                model_settings=model_settings,dependency_versions=dependency_versions,seed=seed,
                feature_names=panel["feature_names"],horizon=horizon,ridge_alpha=alpha,
                missing_label_paths=panel["missing_label_paths"],folds=audit,
                metrics={k:_metrics(v) for k,v in predictions.items()},predictions=predictions,
                research_only=True,approved_for_model_training=False,
                historical_security_identity_verified=False,live_execution_enabled=False,
                broker_order_submission_enabled=False,model_registry_admission=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(train(**json.loads(args.file.read_text(encoding="utf-8"))),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
