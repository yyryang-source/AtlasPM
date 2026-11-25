"""Local synthetic experiment registry. Never authorizes a trading model."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from contextlib import closing

FLAGS = {"research_only": True, "approved_for_model_training": False,
         "model_registry_admission": False, "live_execution_enabled": False,
         "broker_order_submission_enabled": False,
         "historical_security_identity_verified": False}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",",":"),
                      ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def validate_flags(report):
    if not isinstance(report,dict) or any(report.get(k) is not v for k,v in FLAGS.items()):
        raise ValueError("RESEARCH_REGISTRY_PERMISSION_MISMATCH")


def _connect(path):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(path,timeout=30)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("""CREATE TABLE IF NOT EXISTS research_runs (
            run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            kind TEXT NOT NULL, config_json TEXT NOT NULL,
            report_json TEXT NOT NULL, report_sha256 TEXT NOT NULL
        )""")
    except BaseException:
        con.close()
        raise
    return con


def register(path, config, report, *, kind="training"):
    validate_flags(report)
    if kind not in {"training","experiment"}:
        raise ValueError("INVALID_REGISTRY_KIND")
    if not isinstance(config,dict):
        raise ValueError("INVALID_REGISTRY_CONFIG")
    fingerprint=report.get("dataset_sha256")
    if not isinstance(fingerprint,str) or not re.fullmatch("[0-9a-f]{64}",fingerprint):
        raise ValueError("MISSING_DATASET_FINGERPRINT")
    report_hash=digest(report)
    run_id=digest({"kind":kind,"config":config,"report_sha256":report_hash})
    with closing(_connect(path)) as con, con:
        con.execute("""INSERT OR IGNORE INTO research_runs VALUES (?,?,?,?,?,?)""",
                    (run_id,datetime.now(timezone.utc).isoformat(),kind,
                     canonical(config),canonical(report),report_hash))
    load(path,run_id)
    return run_id


def load(path, run_id):
    if not isinstance(run_id,str) or not re.fullmatch("[0-9a-f]{64}",run_id):
        raise ValueError("INVALID_RUN_ID")
    with closing(_connect(path)) as con:
        row=con.execute("""SELECT created_at,kind,config_json,report_json,report_sha256
                           FROM research_runs WHERE run_id=?""",(run_id,)).fetchone()
    if row is None:
        raise ValueError("UNKNOWN_RUN_ID")
    return _validated_run(run_id, row)


def _validated_run(run_id, row):
    if not isinstance(run_id,str) or not re.fullmatch(r"[0-9a-f]{64}",run_id):
        raise ValueError("INVALID_RUN_ID")
    created,kind,config,report,sha=row
    config,report=json.loads(config),json.loads(report)
    validate_flags(report)
    if digest(report)!=sha or digest({"kind":kind,"config":config,"report_sha256":sha})!=run_id:
        raise ValueError("REGISTRY_INTEGRITY_FAILURE")
    return dict(run_id=run_id,created_at=created,kind=kind,config=config,
                report=report,report_sha256=sha,**FLAGS)


def list_runs(path):
    # Stream one consistent read snapshot instead of opening a connection
    # and repeating schema checks for every stored report.
    result=[]
    with closing(_connect(path)) as con:
        for row in con.execute("""SELECT run_id,created_at,kind,config_json,report_json,report_sha256
                                  FROM research_runs ORDER BY created_at,run_id"""):
            record=_validated_run(row[0],row[1:])
            del record["report"]
            result.append(record)
    return result


def local_path(path):
    """CLI writes only under the current checkout's ignored local_data tree."""
    root=Path.cwd().resolve()/"local_data"
    if root.is_symlink():
        raise ValueError("REGISTRY_ROOT_SYMLINK")
    result=Path(path).resolve()
    if not result.is_relative_to(root) or result.suffix!=".sqlite3":
        raise ValueError("REGISTRY_REQUIRES_LOCAL_DATA_SQLITE3")
    return result
