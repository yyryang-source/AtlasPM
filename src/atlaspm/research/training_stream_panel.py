"""Bounded input-session working set for synthetic Parquet research.

Compact sample metadata remains in RAM; feature values and optional sequence
observations live in a scoped local workspace. Never authorizes real data.
"""
from __future__ import annotations
from array import array
import sqlite3
from atlaspm.research.training_compact import CompactSamples,IndexedRows
from atlaspm.research.training_dataset import validate_panel_metadata,validated_sessions,MarketRegimes,WindowLabels


class DiskObservations:
    def __init__(self,path):
        self.con=sqlite3.connect(path)
        try:
            self.con.execute("PRAGMA cache_size=-8192")
            self.con.execute("""CREATE TABLE observations (
                session INTEGER NOT NULL, security TEXT NOT NULL, features BLOB NOT NULL,
                PRIMARY KEY(security,session)) WITHOUT ROWID""")
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


def build_stream_panel(metadata,sessions,horizon,sequence_length,feature_column,observation_store=None,regime_config=None):
    features=validate_panel_metadata(metadata,horizon,sequence_length)
    if feature_column is None or (sequence_length>1 and observation_store is None):
        raise ValueError("STREAMING_REQUIRES_DISK_WORKSPACE")
    tracker=MarketRegimes(**regime_config) if regime_config is not None else None
    dates=[]
    samples=CompactSamples(dates,horizon,len(features),feature_column)
    valid=array("Q")
    labels=WindowLabels(horizon,sequence_length)
    for k,(day,marks) in enumerate(validated_sessions(sessions,features)):
        dates.append(day)
        if tracker is not None:
            tracker.observe(day,marks)
        if observation_store is not None:
            observation_store.append(k,marks)
        for sid,i,x,y in labels.observe(k,marks):
            samples.append(sid,i,x,y)
            if y is not None:
                valid.append(len(samples)-1)
    feature_column.seal()
    return dict(feature_names=features,dates=dates,horizon=horizon,sequence_length=sequence_length,
                prediction_samples=samples,samples=IndexedRows(samples,valid),
                observation_store=observation_store,
                market_regimes=tracker.states if tracker is not None else None,
                incomplete_sequences=labels.incomplete,missing_label_paths=labels.missing,
                unmatured_final_sessions=min(horizon,len(dates)),peak_buffered_sessions=labels.peak,
                research_only=True,approved_for_model_training=False,
                historical_security_identity_verified=False,live_execution_enabled=False)
