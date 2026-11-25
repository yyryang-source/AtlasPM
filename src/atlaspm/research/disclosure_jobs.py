"""Persistent disclosure document jobs, separate from the polling worker."""
from argparse import ArgumentParser
from contextlib import closing
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import time

from atlaspm.data.market_db import private_path
from atlaspm.data.disclosure_documents import capture
from atlaspm.research.alpha_inbox import ingest
from atlaspm.research import event_queue

PATH = Path("local_data/runs/disclosure_jobs.sqlite3")


def _connect(path):
    p = private_path(Path(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p, timeout=10)
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, metadata TEXT NOT NULL,
            state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            receipt TEXT, analysis TEXT, error TEXT, created TEXT NOT NULL)""")
        db.commit()
    except BaseException:
        db.close()
        raise
    return db


def register(source, report, *, path=PATH):
    if source not in {"sec", "fed"} or len(report["events"]) != len(report["metadata"]) or len(report["events"]) > 1000:
        raise ValueError("Invalid discovery batch")
    with closing(_connect(path)) as db, db:
        added = 0
        for event, row in zip(report["events"], report["metadata"]):
            key = event["revision"]
            encoded = json.dumps(row, ensure_ascii=False, sort_keys=True)
            if len(encoded.encode()) > 16384:
                raise ValueError("Oversized disclosure metadata")
            cur = db.execute("INSERT OR IGNORE INTO jobs(id,source,metadata,state,created) VALUES(?,?,?,'pending',?)",
                             (key, source, encoded, datetime.now(UTC).isoformat()))
            added += cur.rowcount
        return added


def work_once(*, model=None, path=PATH, queue_path=event_queue.PATH):
    if model is not None:
        raise ValueError("Use alpha_inbox for unified AI decisions")
    control = event_queue._read_state(queue_path).get(event_queue.CONTROL_KEY, {})
    if control.get("stopped") or control.get("manual_takeover"):
        return {"status": "PAUSED"}
    with closing(_connect(path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT id,source,metadata FROM jobs
                WHERE state='pending'
                ORDER BY created,id LIMIT 1""").fetchone()
            if row is None:
                return {"status": "IDLE"}
            key, source, metadata = row
            db.execute("UPDATE jobs SET state='working', attempts=attempts+1 WHERE id=?", (key,))
        try:
            receipt = capture(source, json.loads(metadata))
            # SEC archive CIK is not an independently verified ticker mapping.
            ingest(provider="sec" if source == "sec" else "federal_reserve",
                   text=receipt["text"], symbols=[], received_at=receipt["retrieved_at"])
            result = None
            state = "captured"
            with db:
                db.execute("UPDATE jobs SET state=?,receipt=?,analysis=?,error=NULL WHERE id=?",
                           (state, json.dumps(receipt), json.dumps(result) if result is not None else None, key))
            return {"status": state.upper(), "job_id": key, "source": source}
        except Exception as exc:
            # Persist failure without leaking document content or response body.
            with db:
                db.execute("UPDATE jobs SET state='failed',error=? WHERE id=?",
                           (type(exc).__name__, key))
            return {"status": "FAILED", "job_id": key, "error_type": type(exc).__name__}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--recover", action="store_true",
                        help="Only after stopping all document workers: requeue interrupted or failed jobs")
    args = parser.parse_args()
    if not 1 <= args.cycles <= 86400 or not 1 <= args.interval <= 300:
        parser.error("Invalid worker bounds")
    if args.recover:
        with closing(_connect(PATH)) as db, db:
            db.execute("UPDATE jobs SET state='pending' WHERE state IN ('working','failed')")
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
