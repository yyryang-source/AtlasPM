"""Market SQLite online backup and isolated restore. No active replacement."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from time import monotonic

from atlaspm.data.api_gateway import _strict_json
from atlaspm.data.market_db import DB_PATH, private_path
from atlaspm.research.training_registry import FLAGS

ROOT=Path("local_data/runs/sqlite_recovery")
SCHEMA="atlaspm_market_sqlite_recovery_v1"
LIMIT=8*1024**3
MINIMUM=1024**2


def _path(path):
    # Windows temporary/profile directories can have a DOS short-name
    # spelling. Normalize the trusted checkout root, not its children.
    cwd=Path.cwd()
    root=cwd.resolve()
    p=Path(path)
    if p.is_absolute():
        if p.is_relative_to(cwd):
            p=root/p.relative_to(cwd)
    else:
        p=root/p
    if ".." in p.parts or not p.is_relative_to(root/"local_data"):
        raise ValueError("SQLITE_RECOVERY_REDIRECTED_PATH")
    if p!=private_path(p) or p.is_symlink():
        raise ValueError("SQLITE_RECOVERY_REDIRECTED_PATH")
    return p


def _identifier(identifier):
    if (not isinstance(identifier,str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}",identifier)
            or identifier.upper() in {"CON","PRN","AUX","NUL",
                                      *(f"COM{i}" for i in range(1,10)),
                                      *(f"LPT{i}" for i in range(1,10))}):
        raise ValueError("SQLITE_RECOVERY_ID")
    return identifier


def _folder(identifier):
    return _path(ROOT/_identifier(identifier))


def _limits(retention_reviewed,max_bytes,timeout_seconds):
    if retention_reviewed is not True:
        raise ValueError("SQLITE_RECOVERY_RETENTION_REVIEW_REQUIRED")
    if (type(max_bytes) is not int or not MINIMUM<=max_bytes<=LIMIT
            or type(timeout_seconds) is not int or not 1<=timeout_seconds<=1800):
        raise ValueError("SQLITE_RECOVERY_BUDGET")
    return monotonic()+timeout_seconds


def _deadline(end):
    if monotonic()>end:
        raise ValueError("SQLITE_RECOVERY_TIMEOUT")


def _hash(path,max_bytes,end):
    h=sha256()
    count=0
    with _path(path).open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):
            _deadline(end)
            count+=len(chunk)
            if count>max_bytes:
                raise ValueError("SQLITE_RECOVERY_BYTE_BUDGET")
            h.update(chunk)
    return h.hexdigest(),count


def _standalone(path):
    for suffix in ("-wal","-shm","-journal"):
        p=Path(str(path)+suffix)
        if p.exists() or p.is_symlink():
            raise ValueError("SQLITE_RECOVERY_UNSEALED_SIDECAR")


def _connect_read(path,*,immutable=False):
    path=_path(path)
    if not path.is_file():
        raise ValueError("SQLITE_RECOVERY_DATABASE_MISSING")
    if immutable:
        _standalone(path)
    return sqlite3.connect(path.as_uri()+"?mode=ro"+("&immutable=1" if immutable else ""),
                           uri=True,timeout=5)


def _shape(con,scope="market"):
    version=con.execute("PRAGMA user_version").fetchone()[0]
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if scope=="market":
        if version!=1 or not {"equity_daily","option_contracts","bonds","bond_quotes","import_log"}<=tables:
            raise ValueError("SQLITE_RECOVERY_MARKET_SCHEMA")
    elif scope=="research_registry":
        allowed={"research_runs","model_artifacts","alpha_events","alpha_review_events"}
        if version!=0 or not tables or not tables<=allowed or (
                tables.intersection({"alpha_events","alpha_review_events"}) and "research_runs" not in tables):
            raise ValueError("SQLITE_RECOVERY_REGISTRY_SCHEMA")
        if con.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone():
            raise ValueError("SQLITE_RECOVERY_REGISTRY_SCHEMA")
    else:
        raise ValueError("SQLITE_RECOVERY_SCOPE")
    return {"user_version":version,"page_size":con.execute("PRAGMA page_size").fetchone()[0],
            "page_count":con.execute("PRAGMA page_count").fetchone()[0]}


def _registry_integrity(con,end):
    """Validate bounded records and journal chains without executing weights."""
    from atlaspm.research.training_registry import _validated_run,digest,validate_flags
    from atlaspm.research.training_artifacts import SCHEMA as artifact_schema,ARTIFACT_MODELS
    from atlaspm.research.alpha_lifecycle import _history,_review_chain
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table,columns in (("research_runs","length(CAST(config_json AS BLOB))+length(CAST(report_json AS BLOB))"),
                          ("model_artifacts","length(CAST(payload AS BLOB))"),
                          ("alpha_events","length(CAST(event_json AS BLOB))"),
                          ("alpha_review_events","length(CAST(event_json AS BLOB))")):
        if table not in tables:
            continue
        n,size,total=con.execute(f"SELECT count(*),max({columns}),sum({columns}) FROM {table}").fetchone()
        if n>10000 or (size or 0)>16*1024**2 or (total or 0)>64*1024**2:
            raise ValueError("SQLITE_RECOVERY_REGISTRY_RECORD_BUDGET")
    if "research_runs" in tables:
        for row in con.execute("SELECT run_id,created_at,kind,config_json,report_json,report_sha256 FROM research_runs"):
            _deadline(end)
            _validated_run(row[0],row[1:])
    if "model_artifacts" in tables:
        for key,raw in con.execute("SELECT artifact_id,payload FROM model_artifacts"):
            _deadline(end)
            record=_strict_json(raw)
            validate_flags(record)
            if digest(record)!=key or record.get("schema")!=artifact_schema or record.get("model") not in ARTIFACT_MODELS:
                raise ValueError("SQLITE_RECOVERY_ARTIFACT_INTEGRITY")
    for table,column,validator in (("alpha_events","version",_history),("alpha_review_events","request_id",_review_chain)):
        if table in tables:
            for (key,) in con.execute(f"SELECT DISTINCT {column} FROM {table}"):
                _deadline(end)
                events=validator(con,key)
                if table=="alpha_events" and not con.execute("SELECT 1 FROM research_runs WHERE run_id=?",(events[0]["run_id"],)).fetchone():
                    raise ValueError("SQLITE_RECOVERY_ORPHAN_ALPHA_RUN")
                if table=="alpha_review_events" and ("alpha_events" not in tables or not con.execute(
                        "SELECT 1 FROM alpha_events WHERE version=?",(events[0]["binding"]["version"],)).fetchone()):
                    raise ValueError("SQLITE_RECOVERY_ORPHAN_ALPHA_REVIEW")


def _check(path,max_bytes,end,scope="market"):
    with closing(_connect_read(path,immutable=True)) as con:
        con.set_progress_handler(lambda: int(monotonic()>end),1000)
        shape=_shape(con,scope)
        if shape["page_size"]*shape["page_count"]>max_bytes:
            raise ValueError("SQLITE_RECOVERY_BYTE_BUDGET")
        if con.execute("PRAGMA integrity_check(1)").fetchall()!=[("ok",)]:
            raise ValueError("SQLITE_RECOVERY_INTEGRITY_CHECK")
        if scope=="research_registry":
            _registry_integrity(con,end)
        _deadline(end)
        return shape


def _new_folder(identifier,required):
    folder=_folder(identifier)
    folder.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(folder.parent).free<required+MINIMUM:
        raise ValueError("SQLITE_RECOVERY_DISK_SPACE")
    folder.mkdir(exist_ok=False)
    return folder


def _result(folder,manifest):
    return {**FLAGS,**manifest,"folder":str(folder.relative_to(Path.cwd().resolve())),
            "integrity_check":"ok","active_database_replaced":False,
            "source_authenticity_verified":False,"license_independently_verified":False,
            "production_recovery_verified":False}


def _seal(folder,*,max_bytes,end,kind,parent_sha=None,scope="market"):
    path=folder/"market.sqlite3"
    shape=_check(path,max_bytes,end,scope)
    checksum,size=_hash(path,max_bytes,end)
    if size!=shape["page_count"]*shape["page_size"] or (parent_sha and checksum!=parent_sha):
        raise ValueError("SQLITE_RECOVERY_FILE_SIZE_OR_HASH")
    manifest={"schema":SCHEMA,"kind":kind,"created_at":datetime.now(UTC).isoformat(),
              "file":"market.sqlite3","sha256":checksum,"bytes":size,**shape,
              "parent_sha256":parent_sha,"scope":scope}
    with _path(folder/"manifest.json").open("xb") as handle:
        handle.write(json.dumps(manifest,sort_keys=True,allow_nan=False).encode())
        handle.flush()
        os.fsync(handle.fileno())
    return _result(folder,manifest)


def backup(identifier,*,retention_reviewed=False,max_bytes=LIMIT,timeout_seconds=1800,registry_path=None):
    end=_limits(retention_reviewed,max_bytes,timeout_seconds)
    scope="market" if registry_path is None else "research_registry"
    source=_path(DB_PATH if registry_path is None else registry_path)
    if source.suffix!=".sqlite3" or source.is_relative_to(_path(ROOT)):
        raise ValueError("SQLITE_RECOVERY_SOURCE_SCOPE")
    with closing(_connect_read(source)) as con:
        shape=_shape(con,scope)
        size=shape["page_size"]*shape["page_count"]
        if size>max_bytes:
            raise ValueError("SQLITE_RECOVERY_BYTE_BUDGET")
        folder=_new_folder(identifier,size)
        path=_path(folder/"market.sqlite3")
        def progress(status,remaining,total):
            _deadline(end)
            if total*shape["page_size"]>max_bytes:
                raise ValueError("SQLITE_RECOVERY_BYTE_BUDGET")
        with closing(sqlite3.connect(path,timeout=5)) as dest:
            con.backup(dest,pages=128,progress=progress,sleep=.05)
            if dest.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower()!="delete":
                raise ValueError("SQLITE_RECOVERY_JOURNAL_MODE")
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())
    return _seal(folder,max_bytes=max_bytes,end=end,kind="backup",scope=scope)


def _verified(identifier,max_bytes,end):
    folder=_folder(identifier)
    with _path(folder/"manifest.json").open("rb") as handle:
        raw=handle.read(16385)
    if len(raw)>16384:
        raise ValueError("SQLITE_RECOVERY_MANIFEST_BUDGET")
    m=_strict_json(raw)
    if not isinstance(m,dict):
        raise ValueError("SQLITE_RECOVERY_MANIFEST_SCHEMA")
    scope=m.get("scope","market")
    if (set(m)-{"scope"}!={"schema","kind","created_at","file","sha256","bytes",
                 "user_version","page_size","page_count","parent_sha256"}
            or m["schema"]!=SCHEMA or m["kind"] not in {"backup","isolated_restore"}
            or m["file"]!="market.sqlite3" or not isinstance(m["sha256"],str)
            or not re.fullmatch(r"[0-9a-f]{64}",m["sha256"])
            or type(m["bytes"]) is not int or not 0<m["bytes"]<=max_bytes
            or scope not in {"market","research_registry"}
            or m["user_version"]!=(1 if scope=="market" else 0)
            or any(type(m[k]) is not int or m[k]<=0 for k in ("page_size","page_count"))
            or m["bytes"]!=m["page_size"]*m["page_count"]
            or (m["kind"]=="backup" and m["parent_sha256"] is not None)
            or (m["kind"]=="isolated_restore" and m["parent_sha256"]!=m["sha256"])):
        raise ValueError("SQLITE_RECOVERY_MANIFEST_SCHEMA")
    stamp=datetime.fromisoformat(m["created_at"])
    if stamp.utcoffset() is None or stamp>datetime.now(UTC):
        raise ValueError("SQLITE_RECOVERY_MANIFEST_TIME")
    path=_path(folder/m["file"])
    expected=(m["sha256"],m["bytes"])
    if _hash(path,max_bytes,end)!=expected:
        raise ValueError("SQLITE_RECOVERY_HASH_MISMATCH")
    shape=_check(path,max_bytes,end,scope)
    if any(m[k]!=v for k,v in shape.items()) or _hash(path,max_bytes,end)!=expected:
        raise ValueError("SQLITE_RECOVERY_CHANGED_DURING_VERIFY")
    return folder,m


def verify(identifier,*,retention_reviewed=False,max_bytes=LIMIT,timeout_seconds=1800):
    end=_limits(retention_reviewed,max_bytes,timeout_seconds)
    folder,m=_verified(identifier,max_bytes,end)
    return _result(folder,m)


def restore_isolated(identifier,destination,*,retention_reviewed=False,max_bytes=LIMIT,timeout_seconds=1800):
    end=_limits(retention_reviewed,max_bytes,timeout_seconds)
    source,m=_verified(identifier,max_bytes,end)
    folder=_new_folder(destination,m["bytes"])
    path=_path(folder/"market.sqlite3")
    h=sha256()
    count=0
    with _path(source/m["file"]).open("rb") as src,path.open("xb") as dest:
        for chunk in iter(lambda:src.read(1024*1024),b""):
            _deadline(end)
            count+=len(chunk)
            if count>m["bytes"]:
                raise ValueError("SQLITE_RECOVERY_SOURCE_CHANGED")
            h.update(chunk)
            dest.write(chunk)
        dest.flush()
        os.fsync(dest.fileno())
    if (h.hexdigest(),count)!=(m["sha256"],m["bytes"]):
        raise ValueError("SQLITE_RECOVERY_SOURCE_CHANGED")
    return _seal(folder,max_bytes=max_bytes,end=end,kind="isolated_restore",parent_sha=m["sha256"],
                 scope=m.get("scope","market"))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("backup","verify","restore-isolated"))
    p.add_argument("identifier")
    p.add_argument("destination",nargs="?")
    p.add_argument("--retention-reviewed",action="store_true")
    p.add_argument("--registry",type=Path,help="Explicit local research registry SQLite source (backup only)")
    p.add_argument("--max-mib",type=int,default=8192)
    p.add_argument("--timeout-seconds",type=int,default=1800)
    a=p.parse_args()
    opts={"retention_reviewed":a.retention_reviewed,"max_bytes":a.max_mib*1024**2,
          "timeout_seconds":a.timeout_seconds}
    if a.registry is not None:
        if a.action!="backup":
            p.error("Registry source applies only to backup")
        opts["registry_path"]=a.registry
    if a.action=="restore-isolated":
        if not a.destination:
            p.error("Isolated restore requires a new destination identifier")
        result=restore_isolated(a.identifier,a.destination,**opts)
    else:
        if a.destination:
            p.error("Destination applies only to restore-isolated")
        result=(backup if a.action=="backup" else verify)(a.identifier,**opts)
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=="__main__":
    main()
