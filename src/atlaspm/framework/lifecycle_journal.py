"""Durable local declared lifecycle receipts; not an authorization ledger."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from atlaspm.data.market_db import private_path
from atlaspm.data.sec_download import _atomic_write
from atlaspm.framework.trading_lifecycle import LifecycleReview, Receipt, Step
from atlaspm.research.training_registry import digest

ROOT = Path("local_data/runs/lifecycle_reviews")
SCHEMA = "atlaspm_declared_lifecycle_journal_v1"
MAX_BYTES = 2 * 1024 * 1024
MAX_RECEIPTS = 1000
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")


def _path(review_id):
    if not isinstance(review_id, str) or not ID.fullmatch(review_id):
        raise ValueError("INVALID_LIFECYCLE_REVIEW_ID")
    # Avoid Windows device names and case-insensitive path aliasing.
    if review_id != review_id.lower() or review_id.upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1,10)),
        *(f"LPT{i}" for i in range(1,10))
    }:
        raise ValueError("REVIEW_ID_REQUIRES_LOWERCASE_PORTABLE_NAME")
    p = private_path(ROOT / (review_id+".json"))
    if p.parent != private_path(ROOT):
        raise ValueError("LIFECYCLE_JOURNAL_PATH_MISMATCH")
    return p


def _body(review):
    if len(review.receipts) > MAX_RECEIPTS:
        raise ValueError("LIFECYCLE_RECEIPT_BUDGET")
    return {"schema":SCHEMA, "review_id":review.review_id,
            "receipts":[{"step":r.step.value,"evidence_ref":r.evidence_ref,
                         "outcome":r.outcome,"note":r.note} for r in review.receipts]}


def _read(p, review_id):
    with p.open("rb") as f:
        raw = f.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError("LIFECYCLE_JOURNAL_BYTE_BUDGET")
    data = json.loads(raw)
    if not isinstance(data,dict) or set(data) != {"body","sha256"}:
        raise ValueError("LIFECYCLE_JOURNAL_SCHEMA")
    body = data["body"]
    if (not isinstance(body,dict) or set(body)!={"schema","review_id","receipts"}
            or body["schema"]!=SCHEMA or body["review_id"]!=review_id
            or digest(body)!=data["sha256"] or not isinstance(body["receipts"],list)
            or len(body["receipts"])>MAX_RECEIPTS):
        raise ValueError("LIFECYCLE_JOURNAL_INTEGRITY")
    # Revalidate every receipt and transition, even if an edited file was rehashed.
    receipts=[]
    for row in body["receipts"]:
        if not isinstance(row,dict) or set(row)!={"step","evidence_ref","outcome","note"}:
            raise ValueError("LIFECYCLE_RECEIPT_SCHEMA")
        receipts.append(Receipt(Step(row["step"]),row["evidence_ref"],row["outcome"],row["note"]))
    return LifecycleReview(review_id,tuple(receipts)),data["sha256"]


def _report(review, revision):
    return {**review.report(),"schema":SCHEMA,"revision_sha256":revision,
            "receipt_count":len(review.receipts),"research_only":True,
            "approved_for_model_training":False,"model_registry_admission":False,
            "broker_order_submission_enabled":False,
            "warning":"Local declared receipts only; digest is not a signature or independent approval."}


def inspect(review_id):
    return _report(*_read(_path(review_id),review_id))


def _mutate(review_id, operation):
    p = _path(review_id)
    p.parent.mkdir(parents=True,exist_ok=True)
    lock = p.with_suffix(".lock")
    fd = os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    try:
        current = _read(p,review_id) if p.exists() else None
        review = operation(current)
        body = _body(review)
        revision = digest(body)
        if current is not None and current[1]==revision:
            return _report(review,revision)
        raw = json.dumps({"body":body,"sha256":revision},sort_keys=True,
                         ensure_ascii=False,allow_nan=False).encode("utf-8")
        if len(raw)>MAX_BYTES:
            raise ValueError("LIFECYCLE_JOURNAL_BYTE_BUDGET")
        def write(handle):
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_write(p,write)
        return _report(review,revision)
    finally:
        lock.unlink(missing_ok=True)


def create(review_id):
    def start(current):
        if current is not None:
            raise ValueError("LIFECYCLE_REVIEW_ALREADY_EXISTS")
        return LifecycleReview(review_id)
    return _mutate(review_id,start)


def append(review_id, receipt, *, expected_revision):
    if not isinstance(receipt,Receipt):
        raise ValueError("LIFECYCLE_RECEIPT_REQUIRED")
    if not isinstance(expected_revision,str) or not re.fullmatch(r"[a-f0-9]{64}",expected_revision):
        raise ValueError("LIFECYCLE_EXPECTED_REVISION_REQUIRED")
    def update(current):
        if current is None:
            raise ValueError("LIFECYCLE_REVIEW_NOT_FOUND")
        review,revision=current
        if revision!=expected_revision:
            # Exact retry is safe only for the immediate last transition from
            # the supplied predecessor, never an arbitrary stale receipt.
            if review.receipts and review.receipts[-1]==receipt:
                predecessor=LifecycleReview(review_id,review.receipts[:-1])
                if digest(_body(predecessor))==expected_revision:
                    return review
            raise ValueError("LIFECYCLE_REVISION_CONFLICT")
        return review.record(receipt)
    return _mutate(review_id,update)
