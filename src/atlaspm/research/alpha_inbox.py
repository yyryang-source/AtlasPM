"""Persistent, source-neutral input inbox and single-model decision worker."""
from argparse import ArgumentParser
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from atlaspm.data.market_db import private_path
from atlaspm.data.api_gateway import _strict_json
from atlaspm.research.financial_events import FLAGS, _digest, _time
from atlaspm.research.unified_alpha_ai import context, decide, validate_model, validate_output
from atlaspm.research import event_queue

PATH = Path("local_data/runs/alpha_inbox.sqlite3")
# Refuse additional writes at capacity; never silently delete retained source data.
MAX_DATABASE_BYTES = 256 * 1024**2
MAX_EVIDENCE = 100000
MAX_DECISIONS = 100000
MAX_SCOPES = 1000
LEASE_SECONDS = 180


def _capacity(db, table, limit):
    if db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= limit:
        raise ValueError("ALPHA_INBOX_CAPACITY: " + table)
    pages = db.execute("PRAGMA page_count").fetchone()[0]
    size = db.execute("PRAGMA page_size").fetchone()[0]
    if pages * size >= MAX_DATABASE_BYTES:
        raise ValueError("ALPHA_INBOX_DATABASE_CAPACITY")


def _report(status, opportunities=()):
    return {**FLAGS, "status": status, "opportunities": list(opportunities)}



def connect(path):
    p = private_path(Path(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p, timeout=10)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, body TEXT NOT NULL, expires TEXT NOT NULL, received TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS evidence_expiry ON evidence(expires)")
        db.execute("CREATE TABLE IF NOT EXISTS source_seen (id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, input_hash TEXT NOT NULL, body TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS batch_latest (scope TEXT PRIMARY KEY, input_hash TEXT NOT NULL, body TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS worker_claims (scope TEXT PRIMARY KEY, token TEXT NOT NULL, expires TEXT NOT NULL)")
        db.commit()
        return db
    except BaseException:
        db.close()
        raise


def ingest(*, provider, text, symbols, received_at, ttl=86400, path=PATH, source_revision=None):
    from atlaspm.data.provider_policy import ROLES, RIGHTS_ENV, RESTRICTED, local_rights, require_enabled
    import os
    if not isinstance(provider, str) or provider not in ROLES:
        raise ValueError("Unknown source")
    require_enabled(provider)
    if provider in RESTRICTED and provider not in RIGHTS_ENV:
        raise ValueError("Source has no approved local AI rights policy")
    if provider in RIGHTS_ENV:
        local_rights(provider, retention=True)
        if os.environ.get("ATLASPM_" + provider.upper() + "_LOCAL_AI_RIGHTS") != "1":
            raise ValueError("Local AI processing rights not attested")
    if not isinstance(text, str) or not 1 <= len(text) <= 24000 or type(ttl) is not int or not 1 <= ttl <= 2592000:
        raise ValueError("Invalid evidence")
    if not isinstance(symbols, list) or len(symbols) > 100:
        raise ValueError("Invalid symbol scope")
    import re
    if any(not isinstance(s,str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}",s) for s in symbols):
        raise ValueError("Invalid symbol")
    received = _time(received_at)
    if received > datetime.now(UTC):
        raise ValueError("Future evidence")
    row = {"provider": provider, "text": text, "symbols": sorted(set(symbols)),
           "received_at": received.isoformat(), "expires_at": (received+timedelta(seconds=ttl)).isoformat()}
    row["evidence_id"] = _digest(row)
    if source_revision is not None and (not isinstance(source_revision,str) or not 1 <= len(source_revision) <= 256):
        raise ValueError("Invalid source revision")
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if source_revision is not None:
            key = _digest([provider, source_revision])
            if db.execute("SELECT 1 FROM source_seen WHERE id=?", (key,)).fetchone():
                return 0
            _capacity(db, "source_seen", MAX_EVIDENCE)
        exists = db.execute("SELECT 1 FROM evidence WHERE id=?", (row["evidence_id"],)).fetchone()
        if not exists:
            _capacity(db, "evidence", MAX_EVIDENCE)
            db.execute("INSERT INTO evidence VALUES(?,?,?,?)",
                       (row["evidence_id"], json.dumps(row, ensure_ascii=False, allow_nan=False),
                        row["expires_at"], row["received_at"]))
        if source_revision is not None:
            db.execute("INSERT INTO source_seen VALUES(?,?)", (key, row["evidence_id"]))
        _capacity(db, "evidence", MAX_EVIDENCE + 1)
        return 0 if exists else 1


def _validated_decision(result, snapshot, model):
    """Recheck cached/injected results at the same boundary as actual model JSON."""
    if (not isinstance(result, dict) or not isinstance(result.get("status"), str)
            or result["status"] not in {"CANDIDATES", "NO_TRADE"}
            or not isinstance(result.get("opportunities"), list) or len(result["opportunities"]) > 20):
        raise ValueError("Invalid AI decision result")
    allowed = set(FLAGS) | {"status", "opportunities", "model", "as_of", "context_sha256",
                            "omitted", "coverage_complete", "response_sha256"}
    if set(result) - allowed or ("coverage_complete" in result and result["coverage_complete"] is not False):
        raise ValueError("Unexpected AI decision fields")
    if any(k in result and result[k] is not v for k, v in FLAGS.items()):
        raise ValueError("AI decision cannot change safety flags")
    if result.get("model", model) != model or result.get("as_of", snapshot["as_of"]) != snapshot["as_of"]:
        raise ValueError("AI decision scope mismatch")
    fields = {"symbol", "action", "evidence_ids", "reason", "valid_seconds"}
    candidates = []
    for item in result["opportunities"]:
        if not isinstance(item, dict) or set(item) != fields | {"expires_at", "option_contract_verified", "candidate_only"}:
            raise ValueError("Invalid AI candidate schema")
        if item["candidate_only"] is not True:
            raise ValueError("AI candidates cannot authorize orders")
        verified = False if isinstance(item["action"], str) and item["action"].startswith("BUY_") else None
        if item["option_contract_verified"] is not verified:
            raise ValueError("AI cannot verify option contracts")
        candidates.append({k: item[k] for k in fields})
    checked = validate_output({"opportunities": candidates}, snapshot)
    if checked != result["opportunities"] or bool(checked) != (result["status"] == "CANDIDATES"):
        raise ValueError("AI decision evidence or expiry mismatch")
    return {**result, **FLAGS, "model": model, "as_of": snapshot["as_of"], "opportunities": checked}


def run_once(*, universe, model, path=PATH, queue_path=event_queue.PATH, inference=decide,
             recheck_seconds=300):
    validate_model(model)
    if type(recheck_seconds) is not int or not 1 <= recheck_seconds <= 3600:
        raise ValueError("Invalid AI recheck interval")
    now = datetime.now(UTC).isoformat()
    context([], as_of=now, universe=universe)
    universe = sorted(universe)
    def paused():
        control = event_queue._read_state(queue_path).get(event_queue.CONTROL_KEY, {})
        return control.get("stopped") or control.get("manual_takeover")
    if paused():
        return _report("PAUSED")
    scope = _digest([model, universe])
    token = uuid4().hex
    with closing(connect(path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            # Claims are transient coordination records, never source evidence.
            db.execute("DELETE FROM worker_claims WHERE expires<=?", (now,))
            claim = db.execute("SELECT expires FROM worker_claims WHERE scope=?", (scope,)).fetchone()
            if claim and _time(claim[0]) > _time(now):
                return _report("WORKER_BUSY")
            if not claim:
                _capacity(db, "worker_claims", MAX_SCOPES)
            db.execute("INSERT INTO worker_claims VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET token=excluded.token,expires=excluded.expires",
                       (scope, token, (_time(now) + timedelta(seconds=LEASE_SECONDS)).isoformat()))
        try:
            raw = db.execute("""SELECT e.body FROM evidence e WHERE expires>? AND received<=?
                AND (json_array_length(json_extract(e.body,'$.symbols'))=0 OR EXISTS (
                    SELECT 1 FROM json_each(e.body,'$.symbols') s
                    JOIN json_each(?) u ON s.value=u.value))
                ORDER BY received DESC,id LIMIT 1001""", (now, now, json.dumps(universe))).fetchall()
            if len(raw) > 1000:
                return _report("CONTEXT_CAPACITY_BLOCKED")
            rows = [_strict_json(body) for (body,) in raw]
            snapshot = context(rows, as_of=now, universe=universe)
            fingerprint = _digest([model, universe, snapshot["evidence"], snapshot["omitted"]])
            previous = db.execute("SELECT input_hash,body FROM batch_latest WHERE scope=?", (scope,)).fetchone()
            if previous and previous[0] == fingerprint:
                result = _strict_json(previous[1])
                evaluated = _time(result.get("as_of", ""))
                if evaluated > _time(now):
                    raise ValueError("Future cached AI decision")
                old_snapshot = {**snapshot, "as_of": evaluated.isoformat()}
                result = _validated_decision(result, old_snapshot, model)
                active = [x for x in result["opportunities"] if _time(x["expires_at"]) > _time(now)]
                # Renew expiring candidates, and reconsider NO_TRADE on a bounded
                # cadence even when source evidence itself has not changed.
                if (len(active) == len(result["opportunities"])
                        and (_time(now) - evaluated).total_seconds() < recheck_seconds):
                    if paused():
                        return _report("PAUSED")
                    return _report("UNCHANGED" if active else "NO_TRADE", active)
            if paused():
                return _report("PAUSED")
            result = _validated_decision(inference(rows, universe=universe, model=model, as_of=now), snapshot, model)
            if paused():
                return _report("PAUSED")
            finished = datetime.now(UTC)
            if any(_time(item["expires_at"]) <= finished for item in result["opportunities"]):
                # Do not store an internally inconsistent CANDIDATES result.
                result = {**result, "opportunities": [item for item in result["opportunities"]
                                                     if _time(item["expires_at"]) > finished]}
                result["status"] = "CANDIDATES" if result["opportunities"] else "NO_TRADE"
            with db:
                db.execute("BEGIN IMMEDIATE")
                claim = db.execute("SELECT token,expires FROM worker_claims WHERE scope=?", (scope,)).fetchone()
                if not claim or claim[0] != token or _time(claim[1]) <= finished:
                    return _report("WORKER_LEASE_EXPIRED")
                _capacity(db, "decisions", MAX_DECISIONS)
                if not previous:
                    _capacity(db, "batch_latest", MAX_SCOPES)
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
                db.execute("INSERT INTO decisions(input_hash,body) VALUES(?,?)", (fingerprint, encoded))
                db.execute("INSERT INTO batch_latest VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET input_hash=excluded.input_hash,body=excluded.body",
                           (scope, fingerprint, encoded))
                _capacity(db, "decisions", MAX_DECISIONS + 1)
            return _report(result["status"], result["opportunities"])
        finally:
            with db:
                db.execute("DELETE FROM worker_claims WHERE scope=? AND token=?", (scope, token))


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument("--model",required=True)
    parser.add_argument("--symbol",action="append",required=True)
    parser.add_argument("--cycles",type=int,default=100)
    parser.add_argument("--interval",type=int,default=5)
    args=parser.parse_args()
    if not 1<=args.cycles<=86400 or not 1<=args.interval<=300:
        parser.error("Invalid runtime bounds")
    for i in range(args.cycles):
        result=run_once(universe=args.symbol,model=args.model)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if result["status"]=="PAUSED":
            break
        if i+1<args.cycles:
            time.sleep(args.interval)


if __name__=="__main__":
    main()
