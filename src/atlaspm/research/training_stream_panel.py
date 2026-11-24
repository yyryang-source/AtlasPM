"""Bounded input-session working set for synthetic Parquet research.

Compact sample metadata remains in RAM; feature values and optional sequence
observations live in a scoped local workspace. Never authorizes real data.
"""
from __future__ import annotations
from array import array
from collections import deque
from contextlib import closing
from datetime import date
import sqlite3
from atlaspm.engine.contracts import finite,timestamp
from atlaspm.research.training_compact import CompactSamples,IndexedRows
from atlaspm.research.training_dataset import HORIZONS


class DiskObservations:
    def __init__(self,path):
        self.con=sqlite3.connect(path)
        try:
            self.con.execute("PRAGMA cache_size=-8192")
            self.con.execute("""CREATE TABLE observations (
                session INTEGER NOT NULL, security TEXT NOT NULL, features BLOB NOT NULL,
                PRIMARY KEY(session,security)) WITHOUT ROWID""")
        except BaseException:
            self.con.close()
            raise

    def append(self,index,marks):
        with self.con:
            self.con.executemany("INSERT INTO observations VALUES (?,?,?)",
                ((index,sid,array("d",x).tobytes()) for sid,(_,x) in marks.items()))

    def sequence(self,index,sid,length):
        rows=[]
        for (raw,) in self.con.execute(
            "SELECT features FROM observations WHERE security=? AND session BETWEEN ? AND ? ORDER BY session",
            (sid,index-length+1,index)):
            values=array("d")
            values.frombytes(raw)
            rows.append(values.tolist())
        if len(rows)!=length:
            raise ValueError("INCOMPLETE_DISK_SEQUENCE")
        return rows

    def close(self):
        self.con.close()


def build_stream_panel(metadata,sessions,horizon,sequence_length,feature_column,observation_store=None):
    if metadata.get("mode")!="synthetic" or metadata.get("synthetic_fixture") is not True:
        raise ValueError("REAL_TRAINING_DATA_NOT_APPROVED")
    if type(horizon) is not int or horizon not in HORIZONS:
        raise ValueError("INVALID_LABEL_HORIZON")
    if type(sequence_length) is not int or not 1<=sequence_length<=252:
        raise ValueError("INVALID_SEQUENCE_LENGTH")
    if feature_column is None or (sequence_length>1 and observation_store is None):
        raise ValueError("STREAMING_REQUIRES_DISK_WORKSPACE")
    features=metadata["feature_names"]
    if not isinstance(features,list) or not features or any(not isinstance(f,str) or not f or f.lower().startswith(("label","future","target")) for f in features) or len(set(features))!=len(features):
        raise ValueError("INVALID_FEATURE_SCHEMA")
    feature_set=set(features)
    dates=[]
    samples=CompactSamples(dates,horizon,len(features),feature_column)
    valid=array("Q")
    window=deque(maxlen=horizon+sequence_length)
    prior=None
    missing=incomplete=peak=0
    for k,row in enumerate(sessions):
        day=date.fromisoformat(row["date"])
        close=timestamp(row["close_at"],"close_at")
        if close.date()!=day or (prior is not None and close<=prior) or (dates and str(day)<=dates[-1]):
            raise ValueError("SESSION_ORDER")
        prior=close
        dates.append(str(day))
        marks={}
        for item in row["assets"]:
            sid=item["security_id"]
            if not isinstance(sid,str) or not sid.startswith("SYN:") or sid in marks:
                raise ValueError("INVALID_OR_DUPLICATE_SYNTHETIC_ID")
            if item.get("source")!="synthetic":
                raise ValueError("UNVERIFIED_SOURCE")
            if not timestamp(item["observed_at"],"observed_at")<=timestamp(item["available_at"],"available_at")<=close:
                raise ValueError("FEATURE_LOOKAHEAD")
            if set(item["features"])!=feature_set:
                raise ValueError("FEATURE_SCHEMA_MISMATCH")
            marks[sid]=(finite(item["close"],"close",minimum=.000001),
                        [finite(item["features"][f],f) for f in features])
        if not marks:
            raise ValueError("EMPTY_SESSION")
        if observation_store is not None:
            observation_store.append(k,marks)
        window.append(marks)
        peak=max(peak,len(window))
        if k<horizon:
            continue
        i=k-horizon
        # The deque contains every required historical and future session,
        # and is never larger than horizon + sequence_length.
        snapshot=tuple(window)
        position=len(snapshot)-horizon-1
        signal=snapshot[position]
        trailing=snapshot[max(0,position-sequence_length+1):position+1]
        forward=snapshot[position+1:]
        for sid,(price,x) in sorted(signal.items()):
            if i+1<sequence_length or any(sid not in day_marks for day_marks in trailing):
                incomplete+=1
                continue
            missing_path=any(sid not in day_marks for day_marks in forward)
            y=None if missing_path else marks[sid][0]/price-1
            samples.append(sid,i,x,y)
            if missing_path:
                missing+=1
            else:
                valid.append(len(samples)-1)
    feature_column.seal()
    return dict(feature_names=features,dates=dates,horizon=horizon,sequence_length=sequence_length,
                prediction_samples=samples,samples=IndexedRows(samples,valid),
                observation_store=observation_store,
                incomplete_sequences=incomplete,missing_label_paths=missing,
                unmatured_final_sessions=min(horizon,len(dates)),peak_buffered_sessions=peak,
                research_only=True,approved_for_model_training=False,
                historical_security_identity_verified=False,live_execution_enabled=False)
