"""Local content-addressed incremental partition backup. No active restoration."""
from __future__ import annotations
import argparse
from datetime import UTC,datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
from time import monotonic

from atlaspm.data.api_gateway import _strict_json
from atlaspm.data.hf_stock5m import validate_month,ROOT as HF
from atlaspm.data.factor_db import ROOT as FACTORS
from atlaspm.data.sqlite_recovery import _path,_identifier,_deadline,_hash
from atlaspm.research.training_registry import FLAGS,digest

ROOT=Path("local_data/runs/lake_recovery")
SCHEMA="atlaspm_partition_recovery_v1"
SCOPES=("raw","rth","all","factors_hf","factors_sqlite")
LIMIT=256*1024**3
MAX_MANIFEST=2*1024**2


def _limits(retention_reviewed,quiescent,max_bytes,timeout_seconds):
    if retention_reviewed is not True or quiescent is not True:
        raise ValueError("LAKE_RECOVERY_REVIEW_AND_QUIESCENCE_REQUIRED")
    if (type(max_bytes) is not int or not 1<=max_bytes<=LIMIT
            or type(timeout_seconds) is not int or not 1<=timeout_seconds<=1800):
        raise ValueError("LAKE_RECOVERY_BUDGET")
    return monotonic()+timeout_seconds


def _plan(months,scopes):
    if (not isinstance(months,list) or not 1<=len(months)<=400
            or not isinstance(scopes,list) or not 1<=len(scopes)<=len(SCOPES)
            or any(not isinstance(x,str) for x in months+scopes)
            or len(set(months))!=len(months) or len(set(scopes))!=len(scopes)
            or not set(scopes)<=set(SCOPES)):
        raise ValueError("LAKE_RECOVERY_SCOPE")
    required=[]
    optional=[]
    for month in sorted(months):
        validate_month(month)
        y,m=month.split("-")
        for scope in sorted(scopes):
            if scope=="raw":
                required.append(HF/"raw"/"data"/f"ohlcv_{month}.parquet")
            elif scope in ("rth","all"):
                part=Path(f"session={scope}")/f"year={y}"/f"month={m}"
                required.append(HF/"parquet"/part/f"ohlcv_{month}.parquet")
                optional.extend(HF/"quality"/part/f"ohlcv_{month}.quarantine.{ext}" for ext in ("json","parquet"))
            else:
                part=Path(f"source={scope.removeprefix('factors_')}")/"session=rth"/f"year={y}"/f"month={m}"
                for kind in ("daily","factors"):
                    p=FACTORS/kind/part/f"{kind}_{month}.parquet"
                    required.extend((p,p.with_suffix(".json")))
    return required,optional


def _manifest(identifier):
    return _path(ROOT/"manifests"/(_identifier(identifier)+".json"))


def _object(key):
    if not isinstance(key,str) or not re.fullmatch(r"[0-9a-f]{64}",key):
        raise ValueError("LAKE_RECOVERY_DIGEST")
    return _path(ROOT/"objects"/key[:2]/key)


def _stat(path):
    s=path.stat()
    if not path.is_file() or s.st_size<=0:
        raise ValueError("LAKE_RECOVERY_EMPTY_OR_NONFILE")
    return (s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_ino)


def _copy(source,destination,expected,end):
    destination.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(destination.parent).free<expected[1]+1024**2:
        raise ValueError("LAKE_RECOVERY_DISK_SPACE")
    h=sha256()
    count=0
    created=False
    try:
        with source.open("rb") as src,destination.open("xb") as dst:
            created=True
            for chunk in iter(lambda:src.read(1024*1024),b""):
                _deadline(end)
                count+=len(chunk)
                if count>expected[1]:
                    raise ValueError("LAKE_RECOVERY_CHANGED_SOURCE")
                h.update(chunk)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if (h.hexdigest(),count)!=expected:
            raise ValueError("LAKE_RECOVERY_CHANGED_SOURCE")
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def _result(manifest):
    return {**FLAGS,"schema":SCHEMA,"manifest_sha256":digest(manifest),
            "file_count":len(manifest["entries"]),
            "logical_bytes":sum(e["bytes"] for e in manifest["entries"]),
            "active_data_replaced":False,"point_in_time_verified":False,
            "source_rights_independently_verified":False,"production_recovery_verified":False}


def backup(identifier,*,months,scopes,retention_reviewed=False,quiescent=False,
           max_bytes=LIMIT,timeout_seconds=1800):
    end=_limits(retention_reviewed,quiescent,max_bytes,timeout_seconds)
    required,optional=_plan(months,scopes)
    p=_manifest(identifier)
    p.parent.mkdir(parents=True,exist_ok=True)
    lock=_path(ROOT/"writer.lock")
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    try:
        if p.exists():
            raise FileExistsError("LAKE_RECOVERY_MANIFEST_EXISTS")
        sources={v.as_posix():_path(v) for v in required}
        optional_paths={v.as_posix():_path(v) for v in optional}
        sources.update({k:v for k,v in optional_paths.items() if v.exists()})
        stats={k:_stat(v) for k,v in sources.items()}
        if sum(v[0] for v in stats.values())>max_bytes:
            raise ValueError("LAKE_RECOVERY_BYTE_BUDGET")
        entries=[]
        reused=written=total=0
        for name,path in sorted(sources.items()):
            expected=_hash(path,max_bytes-total,end)
            total+=expected[1]
            if expected[1]!=stats[name][0] or total>max_bytes:
                raise ValueError("LAKE_RECOVERY_SOURCE_SET_CHANGED")
            obj=_object(expected[0])
            if obj.exists():
                if _hash(obj,max_bytes,end)!=expected:
                    raise ValueError("LAKE_RECOVERY_OBJECT_CORRUPT")
                reused+=expected[1]
            else:
                _copy(path,obj,expected,end)
                written+=expected[1]
            entries.append({"path":name,"sha256":expected[0],"bytes":expected[1]})
        if any(_stat(v)!=stats[k] for k,v in sources.items()) or any(v.exists()!=(k in sources) for k,v in optional_paths.items()):
            raise ValueError("LAKE_RECOVERY_SOURCE_SET_CHANGED")
        manifest={"schema":SCHEMA,"created_at":datetime.now(UTC).isoformat(),
                  "months":sorted(months),"scopes":sorted(scopes),"entries":entries}
        raw=json.dumps({"manifest":manifest,"sha256":digest(manifest)},sort_keys=True,allow_nan=False).encode()
        if len(raw)>MAX_MANIFEST:
            raise ValueError("LAKE_RECOVERY_MANIFEST_BUDGET")
        with p.open("xb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        return {**_result(manifest),"new_object_bytes":written,"reused_object_bytes":reused}
    finally:
        lock.unlink(missing_ok=True)


def _verified(identifier,max_bytes,end):
    with _manifest(identifier).open("rb") as handle:
        raw=handle.read(MAX_MANIFEST+1)
    if len(raw)>MAX_MANIFEST:
        raise ValueError("LAKE_RECOVERY_MANIFEST_BUDGET")
    wrapper=_strict_json(raw)
    if not isinstance(wrapper,dict) or set(wrapper)!={"manifest","sha256"}:
        raise ValueError("LAKE_RECOVERY_MANIFEST_SCHEMA")
    m=wrapper["manifest"]
    if (not isinstance(m,dict) or set(m)!={"schema","created_at","months","scopes","entries"}
            or m["schema"]!=SCHEMA or digest(m)!=wrapper["sha256"]):
        raise ValueError("LAKE_RECOVERY_MANIFEST_INTEGRITY")
    stamp=datetime.fromisoformat(m["created_at"])
    if stamp.utcoffset() is None or stamp>datetime.now(UTC):
        raise ValueError("LAKE_RECOVERY_MANIFEST_TIME")
    required,optional=_plan(m["months"],m["scopes"])
    entries=m["entries"]
    if not isinstance(entries,list) or len(entries)>len(required)+len(optional):
        raise ValueError("LAKE_RECOVERY_ENTRY_BUDGET")
    allowed={p.as_posix() for p in required+optional}
    names=set()
    total=0
    verified={}
    for e in entries:
        if (not isinstance(e,dict) or set(e)!={"path","sha256","bytes"}
                or not isinstance(e["path"],str) or e["path"] not in allowed or e["path"] in names
                or type(e["bytes"]) is not int or e["bytes"]<=0):
            raise ValueError("LAKE_RECOVERY_ENTRY_SCHEMA")
        names.add(e["path"])
        total+=e["bytes"]
        if total>max_bytes:
            raise ValueError("LAKE_RECOVERY_BYTE_BUDGET")
        key=e["sha256"]
        obj=_object(key)
        if key not in verified:
            verified[key]=_hash(obj,max_bytes,end)
        if verified[key]!=(key,e["bytes"]):
            raise ValueError("LAKE_RECOVERY_OBJECT_CORRUPT")
    if not {p.as_posix() for p in required}<=names:
        raise ValueError("LAKE_RECOVERY_REQUIRED_PARTITION_MISSING")
    return m


def verify(identifier,*,retention_reviewed=False,max_bytes=LIMIT,timeout_seconds=1800):
    end=_limits(retention_reviewed,True,max_bytes,timeout_seconds)
    return _result(_verified(identifier,max_bytes,end))


def restore_isolated(identifier,destination,*,retention_reviewed=False,max_bytes=LIMIT,timeout_seconds=1800):
    end=_limits(retention_reviewed,True,max_bytes,timeout_seconds)
    m=_verified(identifier,max_bytes,end)
    root=_path(ROOT/"restores"/_identifier(destination))
    root.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(root.parent).free<sum(e["bytes"] for e in m["entries"])+1024**2:
        raise ValueError("LAKE_RECOVERY_DISK_SPACE")
    root.mkdir(exist_ok=False)
    for e in m["entries"]:
        dest=_path(root/Path(e["path"]))
        _copy(_object(e["sha256"]),dest,(e["sha256"],e["bytes"]),end)
    with (root/"restore_receipt.json").open("x",encoding="utf-8") as out:
        json.dump(_result(m),out,allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    return {**_result(m),"isolated_folder":str(root.relative_to(Path.cwd().resolve()))}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("backup","verify","restore-isolated"))
    p.add_argument("identifier")
    p.add_argument("destination",nargs="?")
    p.add_argument("--month",action="append",dest="months")
    p.add_argument("--scope",action="append",choices=SCOPES,dest="scopes")
    p.add_argument("--retention-reviewed",action="store_true")
    p.add_argument("--quiescent",action="store_true")
    p.add_argument("--max-gib",type=int,default=256)
    p.add_argument("--timeout-seconds",type=int,default=1800)
    a=p.parse_args()
    opts={"retention_reviewed":a.retention_reviewed,"max_bytes":a.max_gib*1024**3,
          "timeout_seconds":a.timeout_seconds}
    if a.action=="backup":
        if a.destination or not a.months or not a.scopes:
            p.error("Backup requires explicit months and scopes, no destination")
        result=backup(a.identifier,months=a.months,scopes=a.scopes,quiescent=a.quiescent,**opts)
    elif a.months or a.scopes or a.quiescent:
        p.error("Month, scope and quiescent apply only to backup")
    elif a.action=="restore-isolated":
        if not a.destination:
            p.error("Isolated restore requires new destination")
        result=restore_isolated(a.identifier,a.destination,**opts)
    else:
        if a.destination:
            p.error("Destination applies only to restore-isolated")
        result=verify(a.identifier,**opts)
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=="__main__":
    main()
