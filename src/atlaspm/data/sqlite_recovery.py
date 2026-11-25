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
    p=Path(path).absolute()
    if p!=private_path(p) or p.is_symlink():
        raise ValueError("SQLITE_RECOVERY_REDIRECTED_PATH")
    return p


def _folder(identifier):
    if (not isinstance(identifier,str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}",identifier)
            or identifier.upper() in {"CON","PRN","AUX","NUL",
                                      *(f"COM{i}" for i in range(1,10)),
                                      *(f"LPT{i}" for i in range(1,10))}):
        raise ValueError("SQLITE_RECOVERY_ID")
    return _path(ROOT/identifier)


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


def _shape(con):
    version=con.execute("PRAGMA user_version").fetchone()[0]
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if version!=1 or not {"equity_daily","option_contracts","bonds","bond_quotes","import_log"}<=tables:
        raise ValueError("SQLITE_RECOVERY_MARKET_SCHEMA")
    return {"user_version":version,"page_size":con.execute("PRAGMA page_size").fetchone()[0],
            "page_count":con.execute("PRAGMA page_count").fetchone()[0]}


def _check(path,max_bytes,end):
    with closing(_connect_read(path,immutable=True)) as con:
        con.set_progress_handler(lambda: int(monotonic()>end),1000)
        shape=_shape(con)
        if shape["page_size"]*shape["page_count"]>max_bytes:
            raise ValueError("SQLITE_RECOVERY_BYTE_BUDGET")
        if con.execute("PRAGMA integrity_check(1)").fetchall()!=[("ok",)]:
            raise ValueError("SQLITE_RECOVERY_INTEGRITY_CHECK")
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


def _seal(folder,*,max_bytes,end,kind,parent_sha=None):
    path=folder/"market.sqlite3"
    shape=_check(path,max_bytes,end)
    checksum,size=_hash(path,max_bytes,end)
    if size!=shape["page_count"]*shape["page_size"] or (parent_sha and checksum!=parent_sha):
        raise ValueError("SQLITE_RECOVERY_FILE_SIZE_OR_HASH")
    manifest={"schema":SCHEMA,"kind":kind,"created_at":datetime.now(UTC).isoformat(),
              "file":"market.sqlite3","sha256":checksum,"bytes":size,**shape,
              "parent_sha256":parent_sha}
    with _path(folder/"manifest.json").open("xb") as handle:
        handle.write(json.dumps(manifest,sort_keys=True,allow_nan=False).encode())
        handle.flush()
        os.fsync(handle.fileno())
    return _result(folder,manifest)


def backup(identifier,*,retention_reviewed=False,max_bytes=LIMIT,timeout_seconds=1800):
    end=_limits(retention_reviewed,max_bytes,timeout_seconds)
    with closing(_connect_read(DB_PATH)) as con:
        shape=_shape(con)
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
    return _seal(folder,max_bytes=max_bytes,end=end,kind="backup")


def _verified(identifier,max_bytes,end):
    folder=_folder(identifier)
    with _path(folder/"manifest.json").open("rb") as handle:
        raw=handle.read(16385)
    if len(raw)>16384:
        raise ValueError("SQLITE_RECOVERY_MANIFEST_BUDGET")
    m=_strict_json(raw)
    if (set(m)!={"schema","kind","created_at","file","sha256","bytes",
                 "user_version","page_size","page_count","parent_sha256"}
            or m["schema"]!=SCHEMA or m["kind"] not in {"backup","isolated_restore"}
            or m["file"]!="market.sqlite3" or not isinstance(m["sha256"],str)
            or not re.fullmatch(r"[0-9a-f]{64}",m["sha256"])
            or type(m["bytes"]) is not int or not 0<m["bytes"]<=max_bytes
            or m["user_version"]!=1
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
    shape=_check(path,max_bytes,end)
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
    return _seal(folder,max_bytes=max_bytes,end=end,kind="isolated_restore",parent_sha=m["sha256"])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("backup","verify","restore-isolated"))
    p.add_argument("identifier")
    p.add_argument("destination",nargs="?")
    p.add_argument("--retention-reviewed",action="store_true")
    p.add_argument("--max-mib",type=int,default=8192)
    p.add_argument("--timeout-seconds",type=int,default=1800)
    a=p.parse_args()
    opts={"retention_reviewed":a.retention_reviewed,"max_bytes":a.max_mib*1024**2,
          "timeout_seconds":a.timeout_seconds}
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
