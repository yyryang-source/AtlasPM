"""Idempotent local synthetic OMS receipts. Never submits broker orders.

Reuses Unified Portfolio Engine. SQLite transaction protects a request ID
from conflicting payloads and duplicate local committed results. A crash
before commit can safely recompute this pure offline simulation.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from atlaspm.engine.unified import run_unified_portfolio, SAFETY
from atlaspm.research.training_registry import canonical, digest, local_path

VERSION="atlaspm_synthetic_oms_receipt_v1"


def _connect(path):
    p=local_path(path)
    p.parent.mkdir(parents=True,exist_ok=True)
    cx=sqlite3.connect(p,timeout=30)
    try:
        cx.execute("""CREATE TABLE IF NOT EXISTS synthetic_oms_receipts(
            request_id TEXT PRIMARY KEY,
            request_sha256 TEXT NOT NULL,
            request_json TEXT NOT NULL,
            report_sha256 TEXT NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        cx.commit()
        return cx
    except BaseException:
        cx.close()
        raise


def _validate_id(value):
    if not isinstance(value,str) or not re.fullmatch(r"SYN:[A-Za-z0-9_.:-]{1,100}",value):
        raise ValueError("INVALID_SYNTHETIC_REQUEST_ID")


def _validate_request(payload):
    if not isinstance(payload,dict) or payload.get("mode")!="synthetic" or payload.get("synthetic_fixture") is not True:
        raise ValueError("EXPLICIT_SYNTHETIC_OMS_REQUIRED")
    if any(k in payload and payload[k] is not v for k,v in SAFETY.items()):
        raise ValueError("SYNTHETIC_OMS_PERMISSION_OVERRIDE")
    canonical(payload)


def _validate_report(report):
    if not isinstance(report,dict) or any(report.get(k) is not v for k,v in SAFETY.items()):
        raise ValueError("SYNTHETIC_OMS_REPORT_PERMISSION_MISMATCH")
    if report.get("decision") not in ("BLOCK","NO_REBALANCE_NEEDED","SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE"):
        raise ValueError("INVALID_SYNTHETIC_OMS_DECISION")
    if report["decision"]=="SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE":
        checks=report.get("reconciliation_checks")
        if not isinstance(checks,dict) or not checks or any(v is not True for v in checks.values()):
            raise ValueError("SYNTHETIC_OMS_RECONCILIATION_FAILED")


def _decode(request_id,row,*,reused):
    req_hash,req_text,rep_hash,rep_text,created=row
    payload,report=json.loads(req_text),json.loads(rep_text)
    _validate_request(payload)
    _validate_report(report)
    if digest(payload)!=req_hash or digest(report)!=rep_hash:
        raise ValueError("SYNTHETIC_OMS_RECEIPT_INTEGRITY_FAILURE")
    return {"schema":VERSION,"request_id":request_id,"request_sha256":req_hash,
            "report_sha256":rep_hash,"created_at":created,"reused":reused,
            "report":report,"broker_side_effects":False,**SAFETY}


def _read(cx,request_id):
    return cx.execute("""SELECT request_sha256,request_json,report_sha256,report_json,created_at
                     FROM synthetic_oms_receipts WHERE request_id=?""",(request_id,)).fetchone()


def execute(path,request_id,payload):
    _validate_id(request_id)
    _validate_request(payload)
    request_hash=digest(payload)
    with closing(_connect(path)) as cx, cx:
        # Serialize one local commit; no external operation is performed in
        # this transaction. On crash SQLite rolls back and re-run is safe.
        cx.execute("BEGIN IMMEDIATE")
        previous=_read(cx,request_id)
        if previous is not None:
            receipt=_decode(request_id,previous,reused=True)
            if receipt["request_sha256"]!=request_hash:
                raise ValueError("SYNTHETIC_OMS_REQUEST_ID_CONFLICT")
            return receipt
        report=run_unified_portfolio(payload)
        _validate_report(report)
        row=(request_hash,canonical(payload),digest(report),canonical(report),
             datetime.now(timezone.utc).isoformat())
        cx.execute("INSERT INTO synthetic_oms_receipts VALUES (?,?,?,?,?,?)",(request_id,*row))
        return _decode(request_id,row,reused=False)


def inspect(path,request_id):
    _validate_id(request_id)
    p=local_path(path)
    if not p.is_file():
        raise ValueError("SYNTHETIC_OMS_RECEIPT_NOT_FOUND")
    with closing(sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,timeout=30)) as cx:
        row=_read(cx,request_id)
    if row is None:
        raise ValueError("SYNTHETIC_OMS_RECEIPT_NOT_FOUND")
    return _decode(request_id,row,reused=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",type=Path,required=True)
    p.add_argument("--request-id",required=True)
    p.add_argument("--file",type=Path)
    a=p.parse_args()
    result=(execute(a.db,a.request_id,json.loads(a.file.read_text(encoding="utf-8")))
            if a.file else inspect(a.db,a.request_id))
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=="__main__":
    main()
