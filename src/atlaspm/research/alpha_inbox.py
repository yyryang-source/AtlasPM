"""Persistent, source-neutral input inbox and single-model decision worker."""
from argparse import ArgumentParser
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import time

from atlaspm.data.market_db import private_path
from atlaspm.research.financial_events import _digest, _time
from atlaspm.research.unified_alpha_ai import context, decide
from atlaspm.research import event_queue

PATH = Path("local_data/runs/alpha_inbox.sqlite3")


def connect(path):
    p = private_path(Path(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p, timeout=10)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, body TEXT NOT NULL, expires TEXT NOT NULL, received TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS evidence_expiry ON evidence(expires)")
        db.execute("CREATE TABLE IF NOT EXISTS source_seen (id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, input_hash TEXT NOT NULL, body TEXT NOT NULL)")
        db.commit()
        return db
    except BaseException:
        db.close()
        raise


def ingest(*, provider, text, symbols, received_at, ttl=86400, path=PATH, source_revision=None):
    from atlaspm.data.provider_policy import ROLES, RIGHTS_ENV, RESTRICTED, local_rights, require_enabled
    import os
    if provider not in ROLES:
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
        if source_revision is not None:
            key = _digest([provider, source_revision])
            if not db.execute("INSERT OR IGNORE INTO source_seen VALUES(?,?)",(key,row["evidence_id"])).rowcount:
                return 0
        cur = db.execute("INSERT OR IGNORE INTO evidence VALUES(?,?,?,?)",
                         (row["evidence_id"], json.dumps(row,ensure_ascii=False),row["expires_at"],row["received_at"]))
        return cur.rowcount


def run_once(*, universe, model, path=PATH, queue_path=event_queue.PATH, inference=decide):
    def paused():
        control = event_queue._read_state(queue_path).get(event_queue.CONTROL_KEY,{})
        return control.get("stopped") or control.get("manual_takeover")
    if paused():
        return {"status":"PAUSED","opportunities":[]}
    now = datetime.now(UTC).isoformat()
    with closing(connect(path)) as db:
        rows = []
        raw = db.execute("SELECT body FROM evidence WHERE expires>? AND received<=? ORDER BY received DESC,id LIMIT 1001",(now,now)).fetchall()
        if len(raw)>1000:
            return {"status":"CONTEXT_CAPACITY_BLOCKED","opportunities":[]}
        for (body,) in raw:
            row=json.loads(body)
            if not row["symbols"] or set(row["symbols"]).issubset(universe):
                rows.append(row)
        snapshot=context(rows,as_of=now,universe=universe)
        fingerprint=_digest([model,universe,snapshot["evidence"],snapshot["omitted"]])
        previous=db.execute("SELECT input_hash,body FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
        if previous and previous[0]==fingerprint:
            result=json.loads(previous[1])
            active=[x for x in result["opportunities"] if _time(x["expires_at"])>_time(now)]
            return {"status":"UNCHANGED" if active else "NO_TRADE","opportunities":active}
        result=inference(rows,universe=universe,model=model,as_of=now)
        if paused():
            return {"status":"PAUSED","opportunities":[]}
        with db:
            db.execute("INSERT INTO decisions(input_hash,body) VALUES(?,?)",(fingerprint,json.dumps(result,ensure_ascii=False)))
        return {"status":result["status"],"opportunities":result["opportunities"]}


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
