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
from atlaspm.research.training_models import MODELS, versions, fit_predict, validate_parameters
from atlaspm.research.training_sequence import SEQUENCE_MODELS, fit_sequence
from atlaspm.research.training_memory import FoldArrays
from atlaspm.research.training_compact import IndexedRows, feature_workspace


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


def train(payload, *, feature_cache_dir=None, **kwargs):
    """Run a synthetic experiment; optional feature cache is scoped to this call."""
    with feature_workspace(feature_cache_dir) as column:
        return _train(payload,feature_column=column,**kwargs)


def train_parquet(root, manifest, *, feature_cache_dir, batch_rows=4096, max_assets_per_session=100000, **kwargs):
    """Stream validated synthetic partitions into the same fold engine."""
    from contextlib import ExitStack, closing
    from atlaspm.research.training_parquet import iter_sessions
    from atlaspm.research.training_stream_panel import build_stream_panel, DiskObservations
    if feature_cache_dir is None:
        raise ValueError('STREAMING_REQUIRES_DISK_WORKSPACE')
    with ExitStack() as stack:
        column=stack.enter_context(feature_workspace(feature_cache_dir))
        length=kwargs.get('sequence_length',1)
        observations=stack.enter_context(closing(DiskObservations(column.path.parent/'observations.sqlite3'))) if length>1 else None
        sessions=iter_sessions(root,manifest,batch_rows=batch_rows,max_assets_per_session=max_assets_per_session)
        panel=build_stream_panel(manifest,sessions,kwargs.get('horizon',5),length,column,observations,regime_config=kwargs.get('regime_ensemble',{}).get('market',{}) if kwargs.get('regime_ensemble') is not None else None,entry_lag_sessions=kwargs.get('entry_lag_sessions',0))
        return _train(manifest,feature_column=column,prepared_panel=panel,**kwargs)


def _train(payload, *, horizon=5, minimum_train_sessions=20, test_sessions=5,
          embargo_sessions=1, ridge_alpha=1., models=None, seed=42, sequence_length=1, sequence_epochs=12, device='cpu', ensemble=None, checkpoint_path=None, batch_size=64, mixed_precision=False, array_budget_mib=None, spill_dir=None, prediction_archive_path=None, feature_column=None, prepared_panel=None, model_artifact_path=None, epoch_checkpoint=False, monitoring=None, regime_ensemble=None, incremental_ridge=False, model_parameters=None, next_session=None, training_window_sessions=None, entry_lag_sessions=0):
    models = ['ridge','historical_mean'] if models is None else models
    if not isinstance(models,list) or not models or any(m not in MODELS+SEQUENCE_MODELS for m in models) or len(set(models))!=len(models):
        raise ValueError('INVALID_MODEL_SELECTION')
    if model_parameters is None:
        model_parameters={}
    if not isinstance(model_parameters,dict) or set(model_parameters)-set(models):
        raise ValueError("INVALID_MODEL_PARAMETER_MAPPING")
    model_parameters={name:validate_parameters(name,p) for name,p in model_parameters.items()}
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise ValueError('INVALID_MODEL_SEED')
    alpha=finite(ridge_alpha,"ridge_alpha",minimum=.000001)
    if any(m in SEQUENCE_MODELS for m in models) and sequence_length < 2:
        raise ValueError('SEQUENCE_MODELS_REQUIRE_MULTIPLE_SESSIONS')
    if type(epoch_checkpoint) is not bool or (epoch_checkpoint and checkpoint_path is None):
        raise ValueError('EPOCH_CHECKPOINT_REQUIRES_FOLD_STORE')
    if epoch_checkpoint and not any(m in SEQUENCE_MODELS for m in models):
        raise ValueError('EPOCH_CHECKPOINT_REQUIRES_SEQUENCE_MODEL')
    if type(incremental_ridge) is not bool or (incremental_ridge and set(models)-{'ridge','historical_mean'}):
        raise ValueError('INCREMENTAL_RIDGE_REQUIRES_LINEAR_BASELINES')
    if incremental_ridge and training_window_sessions is not None:
        raise ValueError('ROLLING_WINDOW_REQUIRES_FULL_REFIT')
    artifact_store=None
    if model_artifact_path is not None:
        from atlaspm.research.training_artifacts import ModelArtifacts,ARTIFACT_MODELS,SCHEMA,FLAGS
        if set(models)-set(ARTIFACT_MODELS):
            raise ValueError('MODEL_ARTIFACT_UNSUPPORTED_ESTIMATOR')
    if regime_ensemble is not None and (not isinstance(regime_ensemble,dict) or set(regime_ensemble)-{'market','weights'} or not all(isinstance(v,dict) for v in regime_ensemble.values())):
        raise ValueError('INVALID_REGIME_ENSEMBLE_CONFIG')
    if next_session is not None and prepared_panel is not None:
        raise ValueError('AS_OF_FORECAST_REQUIRES_OBSERVED_JSON_PANEL')
    panel=prepared_panel if prepared_panel is not None else build_panel(payload,horizon,sequence_length,compact=True,feature_column=feature_column,regime_config=regime_ensemble.get('market',{}) if regime_ensemble is not None else None,next_session=next_session,entry_lag_sessions=entry_lag_sessions)
    if model_artifact_path is not None:
        artifact_store=ModelArtifacts(model_artifact_path)
    folds=iter_purged_folds(panel,minimum_train_sessions=minimum_train_sessions,
                       test_sessions=test_sessions,embargo_sessions=embargo_sessions,training_window_sessions=training_window_sessions)
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
    fingerprint=_fingerprint(payload if next_session is None else {'observed':payload,'next_session':next_session})
    config=dict(horizon=horizon,minimum_train_sessions=minimum_train_sessions,test_sessions=test_sessions,
                embargo_sessions=embargo_sessions,ridge_alpha=alpha,models=models,seed=seed,
                sequence_length=sequence_length,sequence_epochs=sequence_epochs,device=device,ensemble=ensemble,
                batch_size=batch_size,mixed_precision=mixed_precision,array_budget_mib=array_budget_mib)
    if entry_lag_sessions:
        config['entry_lag_sessions']=entry_lag_sessions
    if training_window_sessions is not None:
        config['training_window_sessions']=training_window_sessions
    if next_session is not None:
        config['next_session']=next_session
    if model_parameters:
        config['model_parameters']=model_parameters
    if incremental_ridge:
        config['incremental_ridge']=True
    if regime_ensemble is not None:
        config['regime_ensemble']=regime_ensemble
    if monitoring is not None:
        config['monitoring']=monitoring
    if epoch_checkpoint:
        config['epoch_checkpoint']=True
    if artifact_store is not None:
        config['model_artifact_storage']=str(artifact_store.path)
    store=None
    if checkpoint_path is not None:
        from atlaspm.research.training_checkpoint import FoldStore
        import platform
        source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in Path(__file__).parent.glob("training_*.py")}
        store=FoldStore(checkpoint_path,dict(dataset=fingerprint,config=config,
                       dependencies=dependency_versions,source_hashes=source_hashes,
                       runtime=platform.python_version(),platform=platform.platform()))
    incremental=None
    if incremental_ridge:
        from atlaspm.research.training_models import ExpandingRidge
        incremental=ExpandingRidge(len(panel['feature_names']))
    tabular_required=any(m not in SEQUENCE_MODELS and m!='historical_mean' for m in models)
    ridge_required='ridge' in models
    resumed=0
    # Validate resource settings even when every fold is cached.
    FoldArrays(array_budget_mib,spill_dir)
    for fold_index,fold in enumerate(folds):
        added=None
        if incremental is not None:
            added=incremental.update_prefix(IndexedRows(samples,fold['train']))
        cached=store.load(fold_index) if store is not None else None
        if cached is not None:
            if set(cached["predictions"])!=set(models):
                raise ValueError("CHECKPOINT_MODEL_MISMATCH")
            expected=[(r["security_id"],r["date"],r["label_end_date"],r["y"]) for r in IndexedRows(samples,fold["test"]).metadata()]
            for name, rows in cached["predictions"].items():
                if [(r["security_id"],r["date"],r["label_end_date"],r["actual"]) for r in rows]!=expected:
                    raise ValueError("CHECKPOINT_SAMPLE_MISMATCH")
                for row in rows:
                    finite(row["prediction"],"checkpoint_prediction")
                if archive is None:
                    predictions[name].extend(rows)
                else:
                    archive.append(name,rows)
            if artifact_store is not None:
                ids=cached["audit"].get("model_artifact_ids",{})
                if set(ids)!=set(models):
                    raise ValueError("CHECKPOINT_ARTIFACT_MISMATCH")
                for name,key in ids.items():
                    record=artifact_store.load(key)
                    if record["model"]!=name or record["dataset_sha256"]!=fingerprint or record["fold_index"]!=fold_index:
                        raise ValueError("CHECKPOINT_ARTIFACT_MISMATCH")
            audit.append(cached["audit"])
            model_settings=dict(cached["audit"]["model_settings"])
            resumed+=1
            continue
        with FoldArrays(array_budget_mib,spill_dir) as arrays:
            fold_predictions={m:[] for m in models}
            train_rows=IndexedRows(samples,fold["train"])
            test_rows=IndexedRows(samples,fold["test"])
            x=y=z=block=test=ridge=mean=scale=coef=None
            if incremental is not None:
                mean,scale,coef,intercept=incremental.solve(alpha)
            else:
                y=arrays.rows(train_rows.metadata(),(len(train_rows),),lambda r:r["y"])
                intercept=float(y.mean())
                if tabular_required:
                    x=arrays.rows(train_rows,(len(train_rows),len(panel["feature_names"])),lambda r:r["x"])
                    mean=x.mean(axis=0)
                    x-=mean
                    squared=np.zeros(x.shape[1],dtype=np.float64)
                    for start in range(0,len(x),4096):
                        block=x[start:start+4096]
                        squared+=np.einsum("ij,ij->j",block,block)
                    scale=np.sqrt(squared/len(x))
                    scale=np.where(scale>1e-12,scale,1.)
                    x/=scale
                    z=x
                    if ridge_required:
                        coef=np.linalg.solve(z.T@z+alpha*np.eye(z.shape[1]),z.T@(y-intercept))
            if tabular_required:
                test=arrays.rows(test_rows,(len(test_rows),len(panel["feature_names"])),lambda r:r["x"])
                test-=mean
                test/=scale
                if ridge_required:
                    ridge=test@coef+intercept
            sequence_train=sequence_test=None
            artifact_ids={}
            for name in models:
                saved_weights={}
                if name=="ridge":
                    values=ridge
                    settings={"alpha":alpha}
                elif name=="historical_mean":
                    values=np.full(len(test_rows),intercept)
                    settings={}
                elif name in SEQUENCE_MODELS:
                    if sequence_train is None:
                        def materialize(rows):
                            return arrays.rows(rows.metadata(),(len(rows),sequence_length,len(panel["feature_names"])),lambda row:sequence_for(panel,row))
                        sequence_train=materialize(train_rows)
                        sequence_test=materialize(test_rows)
                    epoch_store=None
                    if epoch_checkpoint:
                        from atlaspm.research.training_checkpoint import EpochStore
                        epoch_store=EpochStore(store,fold_index,name)
                    values,settings=fit_sequence(name,sequence_train,y,sequence_test,seed,
                                                 epochs=sequence_epochs,device=device,batch_size=batch_size,
                                                 mixed_precision=mixed_precision,
                                                 capture_weights=saved_weights.update if artifact_store is not None else None,
                                                 epoch_store=epoch_store)
                else:
                    values,settings=fit_predict(name,z,y,test,seed,
                                                parameters=model_parameters.get(name),capture_weights=saved_weights.update if artifact_store is not None else None)
                if artifact_store is not None:
                    if name in SEQUENCE_MODELS:
                        weights=saved_weights
                        snapshot_settings=settings
                        length=sequence_length
                    else:
                        weights=({'coefficients':coef.tolist() if name=='ridge' else [0.]*len(panel['feature_names'])}
                                 if name in ('ridge','historical_mean') else saved_weights)
                        snapshot_settings=dict(feature_mean=mean.tolist() if mean is not None else [0.]*len(panel["feature_names"]),
                                               feature_scale=scale.tolist() if scale is not None else [1.]*len(panel["feature_names"]),intercept=intercept)
                        length=sequence_length
                    artifact_ids[name]=artifact_store.save(dict(schema=SCHEMA,model=name,
                        dataset_sha256=fingerprint,fold_index=fold_index,feature_names=panel['feature_names'],
                        horizon=horizon,sequence_length=length,dependencies=dependency_versions,
                        **({'entry_lag_sessions':entry_lag_sessions} if entry_lag_sessions else {}),
                        test_start=panel['dates'][fold['test_start_index']],
                        test_end=panel['dates'][fold['test_end_index']],
                        latest_train_label=panel['dates'][fold['latest_training_label_end']],
                        settings=snapshot_settings,weights=weights,**FLAGS))
                if len(values)!=len(test_rows):
                    raise ValueError('MODEL_OUTPUT_ROW_COUNT_MISMATCH:'+name)
                model_settings[name]=settings
                for row,value in zip(test_rows.metadata(),values):
                    base={k:row[k] for k in ("security_id","date","label_end_date")}
                    base["actual"]=row["y"]
                    fold_predictions[name].append(dict(base,prediction=finite(float(value),"prediction")))
            audit.append(dict(test_start=panel["dates"][fold["test_start_index"]],
                              test_end=panel["dates"][fold["test_end_index"]],
                              latest_train_label=panel["dates"][fold["latest_training_label_end"]],
                              training_rows=len(train_rows),test_rows=len(test_rows),
                              feature_mean=mean.tolist() if mean is not None else None,
                              feature_scale=scale.tolist() if scale is not None else None,
                              coefficients=coef.tolist() if coef is not None else None,intercept=intercept,
                              model_settings=dict(model_settings),
                              array_resident_bytes=arrays.resident,array_spilled_bytes=arrays.spilled))
            if incremental is not None:
                audit[-1]["incremental_training_rows_added"]=added
                audit[-1]["incremental_training_rows_total"]=incremental.count
            if artifact_store is not None:
                audit[-1]["model_artifact_ids"]=artifact_ids
            if store is not None:
                store.save(fold_index,dict(audit=audit[-1],
                           predictions=fold_predictions))
            for name,rows in fold_predictions.items():
                if archive is None:
                    predictions[name].extend(rows)
                else:
                    archive.append(name,rows)
            # Release references to fold arrays before allocating the next fold.
            del x,y,z,test,ridge,sequence_train,sequence_test,block
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
    regime_audit=None
    if regime_ensemble is not None:
        from atlaspm.research.training_ensemble import iter_ensemble
        regime_audit=[]
        streams={m:archive.rows(m) if archive is not None else predictions[m] for m in models}
        predictions["regime_ensemble"]=[]
        for rows,weights in iter_ensemble(streams,regimes=panel["market_regimes"],
                                           **regime_ensemble.get("weights",{})):
            regime_audit.append(weights)
            if archive is None:
                predictions["regime_ensemble"].extend(rows)
            else:
                archive.append("regime_ensemble",rows)
    metrics={m:_metrics(archive.rows(m) if archive is not None else rows) for m,rows in predictions.items()}
    monitoring_report=None
    if monitoring is not None:
        if not isinstance(monitoring,dict):
            raise ValueError("INVALID_MONITOR_CONFIG")
        from atlaspm.research.training_monitor import monitor_predictions
        monitoring_report=monitor_predictions(
            {m:archive.rows(m) if archive is not None else rows for m,rows in predictions.items()},
            **monitoring)
    if archive is not None:
        archive.finalize()
    return dict(version="synthetic_return_research_v7",dataset_sha256=fingerprint,
                prediction_storage="sqlite" if archive is not None else "memory",
                feature_storage="memmap" if feature_column is not None else "memory",
                input_storage="parquet_stream" if prepared_panel is not None else "json",
                peak_buffered_sessions=panel.get("peak_buffered_sessions"),
                compact_sample_numeric_bytes=samples.numeric_bytes,
                prediction_archive_path=str(archive.path) if archive is not None else None,
                model_settings=model_settings,dependency_versions=dependency_versions,seed=seed,
                monitoring=monitoring_report,regime_weight_history=regime_audit,
                ensemble_weight_history=ensemble_audit,training_config=config,
                checkpoint_run_id=store.run_id if store else None,resumed_folds=resumed,
                model_artifact_storage=str(artifact_store.path) if artifact_store is not None else None,
                feature_names=panel["feature_names"],horizon=horizon,ridge_alpha=alpha,
                **({'entry_lag_sessions':entry_lag_sessions} if entry_lag_sessions else {}),
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
    config=json.loads(args.file.read_text(encoding="utf-8"))
    if "manifest" in config:
        result=train_parquet(**config)
    else:
        result=train(**config)
    json.dump(result,sys.stdout,indent=2,allow_nan=False)
    print()


if __name__=="__main__":
    main()
