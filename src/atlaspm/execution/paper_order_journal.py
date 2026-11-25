"""SQLite journal for Paper order intents, broker observations and reconciliation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class PaperJournal:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript("""
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS paper_batches (
                    id TEXT PRIMARY KEY, account_hash TEXT NOT NULL, session TEXT NOT NULL,
                    created_at TEXT NOT NULL, baseline TEXT NOT NULL, reconciliation TEXT,
                    UNIQUE(account_hash, session)
                );
                CREATE TABLE IF NOT EXISTS paper_orders (
                    client_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES paper_batches(id),
                    symbol TEXT NOT NULL, ticket TEXT NOT NULL, state TEXT NOT NULL,
                    order_id INTEGER, broker_filled INTEGER, detail TEXT
                );
                CREATE TABLE IF NOT EXISTS paper_executions (
                    execution_id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES paper_orders(client_id),
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_controls (
                    account_hash TEXT PRIMARY KEY, stopped INTEGER NOT NULL CHECK(stopped IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS paper_control_events (
                    id INTEGER PRIMARY KEY, account_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    stopped INTEGER NOT NULL, reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_events (
                    id INTEGER PRIMARY KEY, client_id TEXT NOT NULL REFERENCES paper_orders(client_id),
                    created_at TEXT NOT NULL, state TEXT NOT NULL, detail TEXT
                );
            """)
            yield db

    @staticmethod
    def _require_running(db, account_hash):
        row = db.execute("SELECT stopped FROM paper_controls WHERE account_hash=?", (account_hash,)).fetchone()
        if row is not None and row["stopped"]:
            raise ValueError("Paper submission stopped by operator")

    @staticmethod
    def _require_reconciled(db, account_hash):
        # A terminal broker status alone does not establish cash/fill agreement.
        # Include every earlier session, not just unknown submission results.
        for row in db.execute("SELECT id, reconciliation FROM paper_batches WHERE account_hash=?", (account_hash,)):
            active = db.execute(
                """SELECT client_id FROM paper_orders WHERE batch_id=?
                   AND state NOT IN ('filled','cancelled','rejected','expired','not_sent') LIMIT 1""",
                (row["id"],),
            ).fetchone()
            if active:
                raise ValueError(f"Resolve the ambiguous broker order {active['client_id']} before sending another batch")
            report = json.loads(row["reconciliation"]) if row["reconciliation"] else {}
            if report.get("status") != "matched":
                raise ValueError("Prior Paper batch requires matched broker reconciliation")

    def control(self, account_hash, *, stopped, reason):
        """Persist operator stop/resume; cancellation and reconciliation stay available."""
        if type(stopped) is not bool or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 256:
            raise ValueError("Paper control requires an explicit reason")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not stopped:
                self._require_reconciled(db, account_hash)
            db.execute("INSERT INTO paper_controls VALUES (?,?) ON CONFLICT(account_hash) DO UPDATE SET stopped=excluded.stopped",
                       (account_hash, int(stopped)))
            db.execute("INSERT INTO paper_control_events(account_hash,created_at,stopped,reason) VALUES (?,?,?,?)",
                       (account_hash, _timestamp(), int(stopped), reason.strip()))
        return {"status": "stopped" if stopped else "resumed", "inflight_order_may_remain": True}

    def require_running(self, account_hash):
        with self.connect() as db:
            self._require_running(db, account_hash)

    def claim(self, reviewed: dict, fresh: dict) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_running(db, reviewed["account_id_sha256"])
            self._require_reconciled(db, reviewed["account_id_sha256"])
            try:
                db.execute("""INSERT INTO paper_batches(id, account_hash, session, created_at, baseline)
                              VALUES(?, ?, ?, ?, ?)""", (
                    reviewed["id"], reviewed["account_id_sha256"], reviewed["execution_session"],
                    fresh["observed_at"], _encoded(fresh["next_day_snapshot"]),
                ))
                db.executemany(
                    """INSERT INTO paper_orders(client_id, batch_id, symbol, ticket, state)
                       VALUES(?, ?, ?, ?, 'not_sent')""",
                    [(leg["ticket"]["cOID"], reviewed["id"], leg["symbol"], _encoded(leg["ticket"]))
                     for leg in fresh["broker_estimates"]],
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("A Paper batch for this account/session or client order ID is already journaled") from exc

    @staticmethod
    def _write_state(db, client_id, state, detail, order_id=None, broker_filled=None, expected=None):
        prior = db.execute(
            "SELECT state, order_id, broker_filled, detail FROM paper_orders WHERE client_id=?", (client_id,),
        ).fetchone()
        if prior is None or expected is not None and prior["state"] not in expected:
            raise ValueError("Paper order state changed; refresh broker observations before retrying")
        if (order_id is not None and prior["order_id"] not in (None, order_id)
                or broker_filled is not None and prior["broker_filled"] is not None
                and broker_filled < prior["broker_filled"]):
            raise ValueError("Broker order identity or fill quantity conflicts with the journal")
        values = (state, order_id if order_id is not None else prior["order_id"],
                  broker_filled if broker_filled is not None else prior["broker_filled"], detail, client_id)
        if values[:4] == (prior["state"], prior["order_id"], prior["broker_filled"], prior["detail"]):
            return
        db.execute("UPDATE paper_orders SET state=?, order_id=?, broker_filled=?, detail=? WHERE client_id=?", values)
        db.execute("UPDATE paper_batches SET reconciliation=NULL WHERE id=(SELECT batch_id FROM paper_orders WHERE client_id=?)",
                   (client_id,))
        db.execute("INSERT INTO paper_events(client_id, created_at, state, detail) VALUES(?, ?, ?, ?)",
                   (client_id, _timestamp(), state, detail))

    def transition(self, client_id: str, state: str, detail: str, *,
                   previous: set[str], order_id: int | None = None, broker_filled: int | None = None) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if state == "sending":
                row = db.execute("""SELECT b.account_hash FROM paper_batches b
                    JOIN paper_orders o ON o.batch_id=b.id WHERE o.client_id=?""", (client_id,)).fetchone()
                if row is None:
                    raise ValueError("Unknown Paper order")
                self._require_running(db, row["account_hash"])
            self._write_state(db, client_id, state, detail, order_id=order_id,
                              broker_filled=broker_filled, expected=previous)

    def apply(self, batch_id: str, observations: list[dict], fills: list[dict]) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            origins = {row[0] for row in db.execute("SELECT client_id FROM paper_orders WHERE batch_id=?", (batch_id,))}
            if any(item["client_id"] not in origins for item in observations + fills):
                raise ValueError("Broker evidence belongs to another Paper batch")
            for fill in fills:
                stored = db.execute("SELECT data FROM paper_executions WHERE execution_id=?",
                                    (fill["execution_id"],)).fetchone()
                payload = _encoded(fill)
                if stored is not None and stored["data"] != payload:
                    previous = json.loads(stored["data"])
                    if previous["commission"] is not None or fill["commission"] is None or (
                        previous | {"commission": fill["commission"]}
                    ) != fill:
                        raise ValueError("Broker reused an execution ID with conflicting fill details")
                    db.execute("UPDATE paper_executions SET data=? WHERE execution_id=?", (payload, fill["execution_id"]))
                    db.execute("UPDATE paper_batches SET reconciliation=NULL WHERE id=?", (batch_id,))
                else:
                    if db.execute("INSERT OR IGNORE INTO paper_executions VALUES (?, ?, ?)",
                                  (fill["execution_id"], fill["client_id"], payload)).rowcount:
                        db.execute("UPDATE paper_batches SET reconciliation=NULL WHERE id=?", (batch_id,))
            for observation in observations:
                self._write_state(db, **observation)
            for order in db.execute("SELECT * FROM paper_orders WHERE batch_id=?", (batch_id,)).fetchall():
                cumulative = sum(json.loads(item[0])["shares"] for item in db.execute(
                    "SELECT data FROM paper_executions WHERE client_id=?", (order["client_id"],),
                ))
                quantity = json.loads(order["ticket"])["quantity"]
                if cumulative > quantity or order["state"] == "rejected" and cumulative:
                    raise ValueError("Conflicting cumulative broker execution evidence for Paper ticket")
                if cumulative == quantity and order["state"] not in {"filled", "cancelled", "expired"}:
                    if not any(item["client_id"] == order["client_id"] for item in observations):
                        self._write_state(db, order["client_id"], "filled", "Confirmed by full broker executions",
                                          broker_filled=cumulative)

    def get(self, batch_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM paper_batches WHERE id=?", (batch_id,)).fetchone()
            if row is None:
                return None
            orders = [dict(item) | {"ticket": json.loads(item["ticket"])} for item in db.execute(
                "SELECT * FROM paper_orders WHERE batch_id=? ORDER BY rowid", (batch_id,))]
            fills = [json.loads(item[0]) for item in db.execute(
                """SELECT e.data FROM paper_executions e JOIN paper_orders o ON e.client_id=o.client_id
                   WHERE o.batch_id=? ORDER BY e.execution_id""", (batch_id,))]
        return dict(row) | {"baseline": json.loads(row["baseline"]), "orders": orders, "fills": fills,
                            "reconciliation": json.loads(row["reconciliation"]) if row["reconciliation"] else None}

    def save_reconciliation(self, batch_id: str, report: dict, *, expected_batch=None) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT reconciliation FROM paper_batches WHERE id=?", (batch_id,)).fetchone()
            if row is None:
                raise ValueError("Paper batch not found")
            if expected_batch is not None:
                orders = [dict(item) | {"ticket": json.loads(item["ticket"])} for item in db.execute(
                    "SELECT * FROM paper_orders WHERE batch_id=? ORDER BY rowid", (batch_id,))]
                fills = [json.loads(item[0]) for item in db.execute(
                    """SELECT e.data FROM paper_executions e JOIN paper_orders o ON e.client_id=o.client_id
                       WHERE o.batch_id=? ORDER BY e.execution_id""", (batch_id,))]
                if orders != expected_batch["orders"] or fills != expected_batch["fills"]:
                    raise ValueError("Paper journal changed during reconciliation; poll again")
            if row["reconciliation"] is not None:
                previous = json.loads(row["reconciliation"])
                if datetime.fromisoformat(previous["observed_at"]) > datetime.fromisoformat(report["observed_at"]):
                    return previous
            db.execute("UPDATE paper_batches SET reconciliation=? WHERE id=?", (_encoded(report), batch_id))
            return report
