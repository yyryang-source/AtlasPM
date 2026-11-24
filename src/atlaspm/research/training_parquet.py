"""Read explicitly synthetic Parquet partitions in bounded Arrow batches.

Not a real factor-lake adapter or streaming end-to-end trainer.
"""
from __future__ import annotations
import hashlib
from datetime import date
from pathlib import Path
from atlaspm.engine.contracts import finite, timestamp


def _hash(path):
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(1024*1024),b""):
            digest.update(block)
    return digest.hexdigest()


def iter_sessions(root, manifest, *, batch_rows=4096, max_assets_per_session=100000):
    if manifest.get("mode")!="synthetic" or manifest.get("synthetic_fixture") is not True:
        raise ValueError("REAL_PARQUET_NOT_APPROVED")
    if type(batch_rows) is not int or not 1<=batch_rows<=100000:
        raise ValueError("INVALID_PARQUET_BATCH_ROWS")
    if type(max_assets_per_session) is not int or not 1<=max_assets_per_session<=100000:
        raise ValueError("INVALID_SESSION_ASSET_LIMIT")
    features=manifest["feature_names"]
    base=["date","close_at","security_id","source","observed_at","available_at","close"]
    if not isinstance(features,list) or not features or len(set(features))!=len(features):
        raise ValueError("INVALID_FEATURE_SCHEMA")
    if any(not isinstance(f,str) or not f or f in base or f.lower().startswith(("label","future","target")) for f in features):
        raise ValueError("INVALID_FEATURE_NAME")
    parts=manifest["partitions"]
    if not isinstance(parts,list) or not parts:
        raise ValueError("MISSING_PARQUET_PARTITIONS")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ValueError("INSTALL_RESEARCH_IO_EXTRA") from exc
    root=Path(root).resolve()
    seen=set()
    current=None
    session=None
    ids=set()
    for part in parts:
        path=(root/part["path"]).resolve()
        if not path.is_relative_to(root) or path.suffix!=".parquet" or path in seen:
            raise ValueError("INVALID_PARQUET_PARTITION_PATH")
        seen.add(path)
        if _hash(path)!=part["sha256"]:
            raise ValueError("PARQUET_INTEGRITY_FAILURE")
        file=pq.ParquetFile(path)
        if not set(base+features)<=set(file.schema_arrow.names):
            raise ValueError("PARQUET_SCHEMA_MISMATCH")
        for batch in file.iter_batches(batch_size=batch_rows,columns=base+features,use_threads=False):
            for row in batch.to_pylist():
                day=row["date"]
                if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:
                    raise ValueError("INVALID_PARQUET_DATE")
                close=timestamp(row["close_at"],"close_at")
                if close.date().isoformat()!=day or (current is not None and day<current):
                    raise ValueError("PARQUET_DATE_ORDER")
                if day!=current:
                    if session is not None:
                        yield session
                    current=day
                    ids=set()
                    session=dict(date=day,close_at=row["close_at"],assets=[])
                elif row["close_at"]!=session["close_at"]:
                    raise ValueError("INCONSISTENT_SESSION_CLOSE")
                sid=row["security_id"]
                if not isinstance(sid,str) or not sid.startswith("SYN:") or sid in ids or row["source"]!="synthetic":
                    raise ValueError("INVALID_PARQUET_SECURITY")
                if not timestamp(row["observed_at"],"observed_at")<=timestamp(row["available_at"],"available_at")<=close:
                    raise ValueError("FEATURE_LOOKAHEAD")
                ids.add(sid)
                if len(ids)>max_assets_per_session:
                    raise ValueError("SESSION_ASSET_LIMIT")
                session["assets"].append(dict(security_id=sid,source="synthetic",
                    observed_at=row["observed_at"],available_at=row["available_at"],
                    close=finite(row["close"],"close",minimum=.000001),
                    features={f:finite(row[f],f) for f in features}))
        if _hash(path)!=part["sha256"]:
            raise ValueError("PARQUET_CHANGED_DURING_READ")
    if session is not None:
        yield session
