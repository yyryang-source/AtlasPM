"""Idempotent hand-off of the close scheduler to the read-only account workflow."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlaspm.execution.ibkr_equity_snapshot import collect_cash_equity_snapshot
from atlaspm.execution.market_close import approved_inputs
from atlaspm.rebalance_workbench import Policy, RunStore, _json, exchange_window


def _missing_references(snapshot: dict, references: bytes) -> set[str]:
    priced = {row["symbol"] for row in csv.DictReader(io.StringIO(references.decode("utf-8")))}
    required = snapshot["positions"].keys() | {item["symbol"] for item in snapshot["open_orders"]}
    return required - priced


def draft_after_close(
    directory: Path, audit: Path, store: RunStore, transport, *, account_id: str, policy: Policy,
    prepare_account_marks, now: datetime | None = None,
) -> dict:
    market = json.loads(audit.read_text(encoding="utf-8"))
    result = {"as_of": market["as_of"], "audit": str(audit)}
    if market["status"] != "ready":
        return result | {"status": "market_blocked"}
    if market["rebalance_due"] is not True:
        return result | {"status": "not_scheduled"}
    day, scope, _, references, digest = approved_inputs(audit)
    _, close = exchange_window(day)
    parameters = asdict(policy)
    key = hashlib.sha256(_json([digest, account_id, parameters]).encode()).hexdigest()
    legacy_key = hashlib.sha256(_json([digest, account_id, policy.allocation]).encode()).hexdigest()
    with closing(sqlite3.connect(directory / "draft_handoffs.sqlite3", timeout=2)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS handoffs (id TEXT PRIMARY KEY, run_id TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        saved = db.execute("SELECT run_id FROM handoffs WHERE id IN (?, ?)", (key, legacy_key))
        for (run_id,) in saved:
            report = store.get(run_id)
            if report and report["status"] in {"review", "reviewed"} and report["policy"] == parameters:
                return result | {"status": "review_available", "run_id": run_id}
        observed = now or datetime.now(timezone.utc)
        if not close <= observed <= close + timedelta(hours=4):
            return result | {"status": "outside_account_capture_window"}
        snapshot = collect_cash_equity_snapshot(transport, account_id=account_id, dedicated=True, now=now)
        missing = _missing_references(snapshot, references)
        if missing:
            audit = prepare_account_marks(day, tuple(sorted(missing)))
            updated = json.loads(audit.read_text(encoding="utf-8"))
            if updated["status"] != "ready":
                return result | {"status": "account_marks_blocked", "account_audit": str(audit)}
            updated_day, updated_scope, _, updated_references, _ = approved_inputs(audit)
            if updated_day != day or set(json.loads(updated_scope)["symbols"]) != set(json.loads(scope)["symbols"]):
                raise ValueError("Supplemental account audit changed the session or selection universe")
            if (now or datetime.now(timezone.utc)) > close + timedelta(hours=4):
                return result | {"status": "outside_account_capture_window"}
            snapshot = collect_cash_equity_snapshot(transport, account_id=account_id, dedicated=True, now=now)
            missing = _missing_references(snapshot, updated_references)
            if missing:
                return result | {"status": "account_marks_blocked", "account_audit": str(audit),
                                 "missing_symbols": sorted(missing)}
        run_id = store.run_prepared(
            audit, _json(snapshot).encode(), policy, account_source="local_ibkr_cash_equity_snapshot",
        )
        if store.get(run_id)["status"] == "blocked":
            return result | {"status": "draft_blocked", "run_id": run_id}
        db.execute("INSERT OR REPLACE INTO handoffs VALUES (?, ?)", (key, run_id))
        return result | {"status": "review_available", "run_id": run_id}
