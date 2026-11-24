"""Synthetic expanding-window return prediction, research only."""
from __future__ import annotations
import argparse
import hashlib
import json
from math import sqrt
from pathlib import Path
from statistics import fmean
from itertools import groupby
from atlaspm.research.training_dataset import build_panel, iter_purged_folds, sequence_for
from atlaspm.engine.contracts import finite
from atlaspm.research.training_models import MODELS, versions, fit_predict
from atlaspm.research.training_sequence import SEQUENCE_MODELS, fit_sequence
from atlaspm.research.training_memory import FoldArrays


def _fingerprint(payload):
    digest=hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True,separators=(",",":"),allow_nan=False).iterencode(payload):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


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
    """Evaluate chronologically ordered predictions one session at a time."""
    from math import fsum
    total=0
    count=0
    dates=0
    absolute=[]
    squared=[]
    ics=[]
    rank_ics=[]
    previous=None
    for day, group in groupby(rows,key=lambda r:r["date"]):
        if previous is not None and day<=previous:
            raise ValueError("METRICS_PREDICTIONS_NOT_CHRONOLOGICAL")
        previous=day
        p=[]
        y=[]
        for row in group:
            total+=1
            if row["actual"] is not None:
                p.append(row["prediction"])
                y.append(row["actual"])
        if not y:
            continue
        count+=len(y)
        dates+=1
        absolute.append(fsum(abs(a-b) for a,b in zip(p,y)))
        squared.append(fsum((a-b)**2 for a,b in zip(p,y)))
        ic=_correlation(p,y)
        ric=_correlation(_ranks(p),_ranks(y))
        if ic is not None:
            ics.append(ic)
        if ric is not None:
            rank_ics.append(ric)
    return dict(samples=count,prediction_count=total,missing_actual_count=total-count,
                dates=dates,mae=fsum(absolute)/count if count else None,
                rmse=sqrt(fsum(squared)/count) if count else None,
                mean_daily_ic=fmean(ics) if ics else None,
                mean_daily_rank_ic=fmean(rank_ics) if rank_ics else None,
                valid_ic_dates=len(ics),valid_rank_ic_dates=len(rank_ics))


def train(payload, *, horizon=5, minimum_train_sessions=20, test_sessions=5,
          embargo_sessions=1, ridge_alpha=1., models=None, seed=42, sequence_length=1, sequence_epochs=12, device='cpu', ensemble=None, checkpoint_path=None, batch_size=64, mixed_precision=False, array_budget_mib=None, spill_dir=None, prediction_archive_path=None):
    models = ['ridge','historical_mean'] if models is None else models
    if not isinstance(models,list) or not models or any(m not in MODELS+SEQUENCE_MODELS for m in models) or len(set(models))!=len(models):
        raise ValueError('INVALID_MODEL_SELECTION')
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise ValueError('INVALID_MODEL_SEED')
    alpha=finite(ridge_alpha,"ridge_alpha",minimum=.000001)
    if any(m in SEQUENCE_MODELS for m in models) and sequence_length < 2:
        raise ValueError('SEQUENCE_MODELS_REQUIRE_MULTIPLE_SESSIONS')
    panel=build_panel(payload,horizon,sequence_length,compact=True)
    folds=iter_purged_folds(panel,minimum_train_sessions=minimum_train_sessions,
                       test_sessions=test_sessions,embargo_sessions=embargo_sessions)
    try:
        import numpy as np
    except ImportError as exc:
        raise ValueError("TRAINING_REQUIRES_PORTFOLIO_EXTRA") from exc
    samples=panel["prediction_samples"]
    dependency_versions=versions(models)
    if any(m in SEQUENCE_MODELS for m in models):
        from importlib.metadata import version, PackageNotFoundError
        try:
            dependency_versions['torch']=version('torch')
        except PackageNotFoundError as exc:
            raise ValueError('SEQUENCE_MODEL_REQUIRES_PYTORCH') from exc
    predictions={m:[] for m in models}
    archive=None
    if prediction_archive_path is not None:
        from atlaspm.research.training_predictions import PredictionArchive
        archive=PredictionArchive(prediction_archive_path)
    model_settings={}
    audit=[]
    fingerprint=_fingerprint(payload)
    config=dict(horizon=horizon,minimum_train_sessions=minimum_train_sessions,test_sessions=test_sessions,
                embargo_sessions=embargo_sessions,ridge_alpha=alpha,models=models,seed=seed,
                sequence_length=sequence_length,sequence_epochs=sequence_epochs,device=device,ensemble=ensemble,
                batch_size=batch_size,mixed_precision=mixed_precision,array_budget_mib=array_budget_mib)
    store=None
    if checkpoint_path is not None:
        from atlaspm.research.training_checkpoint import FoldStore
        import platform
        source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in Path(__file__).parent.glob("training_*.py")}
        store=FoldStore(checkpoint_path,dict(dataset=fingerprint,config=config,
                       dependencies=dependency_versions,source_hashes=source_hashes,
                       runtime=platform.python_version(),platform=platform.platform()))
    resumed=0
    # Validate resource settings even when every fold is cached.
    FoldArrays(array_budget_mib,spill_dir)
    for fold_index,fold in enumerate(folds):
        cached=store.load(fold_index) if store is not None else None
        if cached is not None:
            if set(cached["predictions"])!=set(models):
                raise ValueError("CHECKPOINT_MODEL_MISMATCH")
            expected=[(samples[i]["security_id"],samples[i]["date"],samples[i]["label_end_date"],samples[i]["y"]) for i in fold["test"]]
            for name, rows in cached["predictions"].items():
                if [(r["security_id"],r["date"],r["label_end_date"],r["actual"]) for r in rows]!=expected:
                    raise ValueError("CHECKPOINT_SAMPLE_MISMATCH")
                for row in rows:
                    finite(row["prediction"],"checkpoint_prediction")
                if archive is None:
                    predictions[name].extend(rows)
                else:
                    archive.append(name,rows)
            audit.append(cached["audit"])
            model_settings=dict(cached["audit"]["model_settings"])
            resumed+=1
            continue
        with FoldArrays(array_budget_mib,spill_dir) as arrays:
            fold_predictions={m:[] for m in models}
            train_rows=[samples[i] for i in fold["train"]]
            test_rows=[samples[i] for i in fold["test"]]
            x=arrays.rows(train_rows,(len(train_rows),len(panel["feature_names"])),lambda r:r["x"])
            y=arrays.rows(train_rows,(len(train_rows),),lambda r:r["y"])
            mean=x.mean(axis=0)
            scale=x.std(axis=0)
            scale=np.where(scale>1e-12,scale,1.)
            x-=mean
            x/=scale
            z=x
            intercept=float(y.mean())
            coef=np.linalg.solve(z.T@z+alpha*np.eye(z.shape[1]),z.T@(y-intercept))
            test=arrays.rows(test_rows,(len(test_rows),len(panel["feature_names"])),lambda r:r["x"])
            test-=mean
            test/=scale
            ridge=test@coef+intercept
            sequence_train=sequence_test=None
            for name in models:
                if name=="ridge":
                    values=ridge
                    settings={"alpha":alpha}
                elif name=="historical_mean":
                    values=np.full(len(test_rows),intercept)
                    settings={}
                elif name in SEQUENCE_MODELS:
                    if sequence_train is None:
                        def materialize(rows):
                            return arrays.rows(rows,(len(rows),sequence_length,len(panel["feature_names"])),lambda row:sequence_for(panel,row))
                        sequence_train=materialize(train_rows)
                        sequence_test=materialize(test_rows)
                    values,settings=fit_sequence(name,sequence_train,y,sequence_test,seed,
                                                 epochs=sequence_epochs,device=device,batch_size=batch_size,
                                                 mixed_precision=mixed_precision)
                else:
                    values,settings=fit_predict(name,z,y,test,seed)
                model_settings[name]=settings
                for row,value in zip(test_rows,values):
                    base={k:row[k] for k in ("security_id","date","label_end_date")}
                    base["actual"]=row["y"]
                    fold_predictions[name].append(dict(base,prediction=finite(float(value),"prediction")))
            audit.append(dict(test_start=panel["dates"][fold["test_start_index"]],
                              test_end=panel["dates"][fold["test_end_index"]],
                              latest_train_label=panel["dates"][fold["latest_training_label_end"]],
                              training_rows=len(train_rows),test_rows=len(test_rows),
                              feature_mean=mean.tolist(),feature_scale=scale.tolist(),
                              coefficients=coef.tolist(),intercept=intercept,
                              model_settings=dict(model_settings),
                              array_resident_bytes=arrays.resident,array_spilled_bytes=arrays.spilled))
            if store is not None:
                store.save(fold_index,dict(audit=audit[-1],
                           predictions=fold_predictions))
            for name,rows in fold_predictions.items():
                if archive is None:
                    predictions[name].extend(rows)
                else:
                    archive.append(name,rows)
    ensemble_audit=None
    if ensemble is not None:
        if not isinstance(ensemble,dict):
            raise ValueError("INVALID_ENSEMBLE_CONFIG")
        from atlaspm.research.training_ensemble import iter_ensemble
        ensemble_audit=[]
        streams=predictions if archive is None else {m:archive.rows(m) for m in models}
        predictions["adaptive_ensemble"]=[]
        for rows,weights in iter_ensemble({m:streams[m] for m in models},**ensemble):
            ensemble_audit.append(weights)
            if archive is None:
                predictions["adaptive_ensemble"].extend(rows)
            else:
                archive.append("adaptive_ensemble",rows)
    metrics={m:_metrics(archive.rows(m) if archive is not None else rows) for m,rows in predictions.items()}
    if archive is not None:
        archive.finalize()
    return dict(version="synthetic_return_research_v7",dataset_sha256=fingerprint,
                prediction_storage="sqlite" if archive is not None else "memory",
                prediction_archive_path=str(archive.path) if archive is not None else None,
                model_settings=model_settings,dependency_versions=dependency_versions,seed=seed,
                ensemble_weight_history=ensemble_audit,training_config=config,
                checkpoint_run_id=store.run_id if store else None,resumed_folds=resumed,
                feature_names=panel["feature_names"],horizon=horizon,ridge_alpha=alpha,
                sequence_length=sequence_length,incomplete_sequences=panel['incomplete_sequences'],
                missing_label_paths=panel["missing_label_paths"],folds=audit,
                metrics=metrics,predictions=predictions if archive is None else None,
                research_only=True,approved_for_model_training=False,
                historical_security_identity_verified=False,live_execution_enabled=False,
                broker_order_submission_enabled=False,model_registry_admission=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",required=True,type=Path)
    args=parser.parse_args()
    import sys
    json.dump(train(**json.loads(args.file.read_text(encoding="utf-8"))),sys.stdout,indent=2,allow_nan=False)
    print()


if __name__=="__main__":
    main()
