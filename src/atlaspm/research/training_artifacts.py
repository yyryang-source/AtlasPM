"""Content-addressed numeric model snapshots for synthetic research only.

No pickle, arbitrary classes, downloaded weights or broker order integration.
The database is local and hashes detect corruption, not malicious authorship.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from importlib.metadata import version
import json
from pathlib import Path
import re
import sqlite3
from atlaspm.research.training_registry import FLAGS,canonical,digest,local_path,validate_flags
from atlaspm.research.training_dataset import validate_panel_metadata,validated_sessions
from atlaspm.research.training_sequence import SEQUENCE_MODELS

from atlaspm.research.training_models import MODELS,PACKAGES,load_estimator_predictor

ARTIFACT_MODELS=MODELS+SEQUENCE_MODELS
SCHEMA="atlaspm_numeric_model_snapshot_v1"


class ModelArtifacts:
    def __init__(self,path):
        self.path=local_path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as con,con:
            con.execute("""CREATE TABLE IF NOT EXISTS model_artifacts (
                artifact_id TEXT PRIMARY KEY, payload TEXT NOT NULL) WITHOUT ROWID""")

    def connect(self):
        return sqlite3.connect(self.path,timeout=30)

    def save(self,record):
        validate_flags(record)
        if record.get("schema")!=SCHEMA or record.get("model") not in ARTIFACT_MODELS:
            raise ValueError("UNSUPPORTED_MODEL_ARTIFACT")
        key=digest(record)
        with closing(self.connect()) as con,con:
            con.execute("INSERT OR IGNORE INTO model_artifacts VALUES (?,?)",(key,canonical(record)))
        self.load(key)
        return key

    def load(self,key):
        if not isinstance(key,str) or not re.fullmatch("[0-9a-f]{64}",key):
            raise ValueError("INVALID_ARTIFACT_ID")
        with closing(self.connect()) as con:
            row=con.execute("SELECT payload FROM model_artifacts WHERE artifact_id=?",(key,)).fetchone()
        if row is None:
            raise ValueError("UNKNOWN_MODEL_ARTIFACT")
        record=json.loads(row[0])
        if digest(record)!=key:
            raise ValueError("ARTIFACT_INTEGRITY_FAILURE")
        validate_flags(record)
        if record.get("schema")!=SCHEMA or record.get("model") not in ARTIFACT_MODELS:
            raise ValueError("UNSUPPORTED_MODEL_ARTIFACT")
        return record

    def list(self):
        with closing(self.connect()) as con:
            keys=[r[0] for r in con.execute("SELECT artifact_id FROM model_artifacts ORDER BY artifact_id")]
        return [dict(artifact_id=key,**{k:v for k,v in self.load(key).items() if k!="weights"})
                for key in keys]


def predict_snapshot(record,payload,*,batch_size=128):
    """Reproduce predictions on observed synthetic feature windows; no labels read."""
    import numpy as np
    validate_flags(record)
    if record.get("schema")!=SCHEMA or record.get("model") not in ARTIFACT_MODELS:
        raise ValueError("UNSUPPORTED_MODEL_ARTIFACT")
    if type(batch_size) is not int or not 1<=batch_size<=8192:
        raise ValueError("INVALID_ARTIFACT_BATCH_SIZE")
    name=record["model"]
    length=record["sequence_length"]
    names=validate_panel_metadata(payload,record["horizon"],length)
    if names!=record["feature_names"]:
        raise ValueError("ARTIFACT_FEATURE_ORDER_MISMATCH")
    if version("numpy")!=record["dependencies"]["numpy"]:
        raise ValueError("ARTIFACT_DEPENDENCY_MISMATCH:numpy")
    sessions=list(validated_sessions(payload["sessions"],names))
    if len(sessions)!=length:
        raise ValueError("ARTIFACT_REQUIRES_EXACT_TRAILING_WINDOW")
    day,latest=sessions[-1]
    if day<record["test_start"] or day<=record["latest_train_label"]:
        raise ValueError("ARTIFACT_LOOKAHEAD")
    settings=record["settings"]
    ids=sorted(sid for sid in latest if all(sid in marks for _,marks in sessions))
    if not ids:
        raise ValueError("NO_ELIGIBLE_ARTIFACT_ROWS")
    out=[]
    if name in SEQUENCE_MODELS:
        import torch
        from atlaspm.research.training_sequence import build_sequence_model
        if version("torch")!=record["dependencies"]["torch"]:
            raise ValueError("ARTIFACT_DEPENDENCY_MISMATCH:torch")
        with torch.random.fork_rng(devices=[]):
            model=build_sequence_model(name,len(names),length)
        target=model.state_dict()
        weights=record["weights"]
        if set(weights)!=set(target):
            raise ValueError("ARTIFACT_TENSOR_KEYS")
        for key,tensor in target.items():
            value=torch.as_tensor(weights[key],dtype=tensor.dtype)
            if value.shape!=tensor.shape or not torch.isfinite(value).all():
                raise ValueError("ARTIFACT_TENSOR_INVALID")
            target[key]=value
        model.load_state_dict(target,strict=True)
        model.eval()
        mean=torch.tensor(settings["feature_mean"],dtype=torch.float32)
        scale=torch.tensor(settings["feature_scale"],dtype=torch.float32)
        if mean.shape!=(len(names),) or scale.shape!=mean.shape or not torch.isfinite(mean).all() or not torch.isfinite(scale).all() or not (scale>0).all():
            raise ValueError("ARTIFACT_NORMALIZATION_INVALID")
        for start in range(0,len(ids),batch_size):
            subset=ids[start:start+batch_size]
            x=torch.tensor([[marks[sid][1] for _,marks in sessions] for sid in subset],dtype=torch.float32)
            with torch.inference_mode():
                vals=model((x-mean)/scale).numpy().astype(float)*settings["target_scale"]+settings["target_mean"]
            out.extend(zip(subset,map(float,vals)))
    else:
        mean=np.asarray(settings["feature_mean"],dtype=float)
        scale=np.asarray(settings["feature_scale"],dtype=float)
        if any(v.shape!=(len(names),) or not np.isfinite(v).all() for v in (mean,scale)) or not (scale>0).all():
            raise ValueError("ARTIFACT_NORMALIZATION_INVALID")
        if name in PACKAGES:
            package=PACKAGES[name]
            if version(package)!=record["dependencies"][package]:
                raise ValueError("ARTIFACT_DEPENDENCY_MISMATCH:"+package)
            weights=record["weights"]
            predict=load_estimator_predictor(name,weights["model"],len(names))
            offset=weights["target_mean"]
            multiplier=weights["target_scale"]
        else:
            coef=np.asarray(record["weights"]["coefficients"],dtype=float)
            if coef.shape!=(len(names),) or not np.isfinite(coef).all():
                raise ValueError("ARTIFACT_LINEAR_INVALID")
            predict=lambda x:x@coef
            offset=settings["intercept"]
            multiplier=1.
        for start in range(0,len(ids),batch_size):
            subset=ids[start:start+batch_size]
            x=np.asarray([latest[sid][1] for sid in subset],dtype=float)
            vals=np.asarray(predict((x-mean)/scale),dtype=float)*multiplier+offset
            out.extend(zip(subset,map(float,vals)))
    if not all(np.isfinite(v) for _,v in out):
        raise ValueError("NONFINITE_ARTIFACT_PREDICTION")
    return dict(model=name,date=day,predictions=[dict(security_id=sid,prediction=v) for sid,v in out],**FLAGS)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",required=True)
    sub=parser.add_subparsers(dest="action",required=True)
    sub.add_parser("list")
    show=sub.add_parser("show")
    show.add_argument("artifact_id")
    predict=sub.add_parser("predict")
    predict.add_argument("artifact_id")
    predict.add_argument("--file",required=True,type=Path)
    args=parser.parse_args()
    store=ModelArtifacts(args.db)
    if args.action=="list":
        report=store.list()
    elif args.action=="show":
        report=store.load(args.artifact_id)
    else:
        report=predict_snapshot(store.load(args.artifact_id),json.loads(args.file.read_text(encoding="utf-8")))
    import sys
    json.dump(report,sys.stdout,indent=2,allow_nan=False)
    print()


if __name__=="__main__":
    main()
