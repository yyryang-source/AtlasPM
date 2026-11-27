"""Persistent disclosure document jobs, separate from the polling worker."""
from argparse import ArgumentParser
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from atlaspm.data.market_db import private_path
from atlaspm.data.disclosure_documents import capture, document_url
from atlaspm.research.alpha_inbox import ingest
from atlaspm.research import event_queue
from atlaspm.research.financial_events import _time, _digest, normalize

PATH = Path("local_data/runs/disclosure_jobs.sqlite3")
MAX_JOBS = 10000
MAX_ATTEMPTS = 5


def _connect(path):
    p = private_path(Path(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p, timeout=10)
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, metadata TEXT NOT NULL,
            state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            receipt TEXT, analysis TEXT, error TEXT, created TEXT NOT NULL)""")
        # In-place migration of the existing job store. Serialize migrations
        # and claims across processes instead of resetting active workers.
        db.execute("BEGIN IMMEDIATE")
        columns = {r[1] for r in db.execute("PRAGMA table_info(jobs)")}
        for name in ("lease_owner", "lease_expires"):
            if name not in columns:
                db.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state,created,id)")
        db.commit()
    except BaseException:
        db.close()
        raise
    return db


def register(source, report, *, path=PATH):
    if (source not in {"sec", "fed"} or not isinstance(report, dict)
            or not isinstance(report.get("events"), list) or not isinstance(report.get("metadata"), list)
            or len(report["events"]) != len(report["metadata"]) or len(report["events"]) > 1000):
        raise ValueError("Invalid discovery batch")
    checked = []
    for event, row in zip(report["events"], report["metadata"]):
        if not isinstance(event, dict) or not isinstance(row, dict):
            raise ValueError("Invalid disclosure record")
        raw = {k: v for k, v in event.items() if k not in {"received_at", "revision", "event_id", "rights_status"}}
        if normalize(raw, received_at=event["received_at"]) != event:
            raise ValueError("Unvalidated disclosure event")
        if event["provider"] != ("sec" if source == "sec" else "federal_reserve"):
            raise ValueError("Disclosure source mismatch")
        metadata_digest = _digest({k: v for k, v in row.items() if k != "evidence_sha256"})
        if event["evidence_sha256"] != metadata_digest:
            raise ValueError("Disclosure metadata digest mismatch")
        document_url(source, row)
        encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > 16384:
            raise ValueError("Oversized disclosure metadata")
        checked.append((event["revision"], encoded))
    with closing(_connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        added = 0
        for key, encoded in checked:
            old = db.execute("SELECT source,metadata FROM jobs WHERE id=?", (key,)).fetchone()
            if old is not None and old != (source, encoded):
                raise ValueError("DISCLOSURE_REVISION_CONFLICT")
            cur = db.execute("INSERT OR IGNORE INTO jobs(id,source,metadata,state,created) VALUES(?,?,?,'pending',?)",
                             (key, source, encoded, datetime.now(UTC).isoformat()))
            added += cur.rowcount
        if db.execute("SELECT count(*) FROM jobs").fetchone()[0] > MAX_JOBS:
            raise ValueError("DISCLOSURE_JOB_CAPACITY")
        return added


def recover(*, path=PATH, as_of=None, retry_failed=True):
    """Requeue expired claims and bounded failed jobs; never steal active work.

    Legacy working rows without leases remain blocked: their owners cannot be
    proven stopped by this store. An operator must inspect those separately.
    """
    if type(retry_failed) is not bool:
        raise ValueError("Invalid recovery policy")
    now = datetime.now(UTC) if as_of is None else _time(as_of)
    if now > datetime.now(UTC):
        raise ValueError("Future recovery cutoff")
    with closing(_connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        expired = db.execute("""UPDATE jobs SET state='pending',lease_owner=NULL,
            lease_expires=NULL WHERE state='working' AND lease_expires<=?
            AND attempts<?""", (now.isoformat(), MAX_ATTEMPTS)).rowcount
        failed = 0
        if retry_failed:
            failed = db.execute("""UPDATE jobs SET state='pending',lease_owner=NULL,
                lease_expires=NULL WHERE state='failed' AND attempts<?""",
                (MAX_ATTEMPTS,)).rowcount
        blocked = db.execute("""SELECT count(*) FROM jobs WHERE state='working'
            AND lease_expires IS NULL""").fetchone()[0]
        exhausted = db.execute("""SELECT count(*) FROM jobs
            WHERE state IN ('working','failed') AND attempts>=?""", (MAX_ATTEMPTS,)).fetchone()[0]
    return {"status": "RECOVERY_REVIEW_REQUIRED" if blocked or exhausted else "RECOVERED",
            "expired_requeued": expired, "failed_requeued": failed,
            "legacy_working_blocked": blocked, "attempts_exhausted": exhausted}


def work_once(*, model=None, path=PATH, queue_path=event_queue.PATH,
              inbox_path=None, lease_seconds=600):
    if model is not None:
        raise ValueError("Use alpha_inbox for unified AI decisions")
    if type(lease_seconds) is not int or not 60 <= lease_seconds <= 3600:
        raise ValueError("Invalid document worker lease")
    control = event_queue._read_state(queue_path).get(event_queue.CONTROL_KEY, {})
    if control.get("stopped") or control.get("manual_takeover"):
        return {"status": "PAUSED"}
    with closing(_connect(path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            now = datetime.now(UTC)
            db.execute("""UPDATE jobs SET state='pending',lease_owner=NULL,lease_expires=NULL
                WHERE state='working' AND lease_expires<=? AND attempts<?""",
                (now.isoformat(), MAX_ATTEMPTS))
            row = db.execute("""SELECT id,source,metadata FROM jobs
                WHERE state='pending' AND attempts<?
                ORDER BY created,id LIMIT 1""", (MAX_ATTEMPTS,)).fetchone()
            if row is None:
                return {"status": "IDLE"}
            key, source, metadata = row
            owner = uuid4().hex
            db.execute("""UPDATE jobs SET state='working', attempts=attempts+1,
                lease_owner=?,lease_expires=? WHERE id=?""",
                (owner, (now+timedelta(seconds=lease_seconds)).isoformat(), key))
        try:
            receipt = capture(source, json.loads(metadata))
            with db:
                db.execute("BEGIN IMMEDIATE")
                claim = db.execute("SELECT lease_owner,lease_expires,state FROM jobs WHERE id=?", (key,)).fetchone()
                if claim[0] != owner or claim[2] != "working" or _time(claim[1]) <= datetime.now(UTC):
                    return {"status": "LEASE_LOST", "job_id": key}
                control = event_queue._read_state(queue_path).get(event_queue.CONTROL_KEY, {})
                if control.get("stopped") or control.get("manual_takeover"):
                    db.execute("UPDATE jobs SET state='pending',lease_owner=NULL,lease_expires=NULL WHERE id=?", (key,))
                    return {"status": "PAUSED", "job_id": key}
                # The stable revision makes a crash after inbox commit safe to
                # retry. Archive CIK is not a verified ticker association.
                ingest(provider="sec" if source == "sec" else "federal_reserve",
                       text=receipt["text"], symbols=[], received_at=receipt["retrieved_at"],
                       source_revision="disclosure:"+key,
                       **({"path": inbox_path} if inbox_path is not None else {}))
                db.execute("""UPDATE jobs SET state='captured',receipt=?,analysis=NULL,error=NULL,
                    lease_owner=NULL,lease_expires=NULL WHERE id=? AND lease_owner=?""",
                    (json.dumps(receipt,allow_nan=False), key, owner))
            return {"status": "CAPTURED", "job_id": key, "source": source}
        except Exception as exc:
            # Persist failure without leaking document content or response body.
            with db:
                db.execute("""UPDATE jobs SET state='failed',error=?,lease_owner=NULL,
                    lease_expires=NULL WHERE id=? AND lease_owner=?""",
                    (type(exc).__name__, key, owner))
            return {"status": "FAILED", "job_id": key, "error_type": type(exc).__name__}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--recover", action="store_true",
                        help="Requeue expired leases and failed jobs within the attempt budget")
    args = parser.parse_args()
    if not 1 <= args.cycles <= 86400 or not 1 <= args.interval <= 300:
        parser.error("Invalid worker bounds")
    if args.recover:
        print(json.dumps(recover()), flush=True)
    for index in range(args.cycles):
        report = work_once()
        print(json.dumps(report), flush=True)
        if report["status"] == "PAUSED":
            return 0
        if index + 1 < args.cycles:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
