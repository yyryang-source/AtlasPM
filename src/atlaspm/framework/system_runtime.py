"""One bounded workstation worker around existing research services.

The worker never calls a broker or promotes a model. Memory/database budgets
are checked between calls; they are admission bounds, not an OS sandbox.
"""
from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
import importlib.util
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from atlaspm.data.api_gateway import _strict_json
from atlaspm.data.market_db import DB_PATH, private_path
from atlaspm.data.sec_download import _atomic_write
from atlaspm.framework import configuration
from atlaspm.research import event_queue
from atlaspm.research.financial_events import _time

ROOT = Path("local_data/runs/system")
HEARTBEAT = ROOT / "heartbeat.json"
LOCK = ROOT / "worker.lock"
FLAGS = {"live_execution_enabled": False, "broker_side_effects": False,
         "formal_training_enabled": False, "data_certified": False}


class WorkerLease:
    """OS-owned lock: process death releases it without deleting another lock."""
    def __enter__(self):
        p = private_path(LOCK)
        p.parent.mkdir(parents=True, exist_ok=True)
        if LOCK.is_symlink():
            raise ValueError("RUNTIME_LOCK_REDIRECTED")
        self.handle = p.open("a+b")
        try:
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise ValueError("RUNTIME_ALREADY_RUNNING") from None
        return self

    def __exit__(self, *_):
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


def setup(path=configuration.DEFAULT_PATH):
    """Idempotently initialize existing stores; preserve operator pause state."""
    from atlaspm.data.market_db import connect
    from atlaspm.research.alpha_inbox import connect as inbox_connect, PATH as inbox_path
    from atlaspm.research.disclosure_jobs import _connect, PATH as job_path
    p = private_path(Path(path))
    created = not p.exists()
    if created:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("x", encoding="utf-8") as handle:
            handle.write(configuration.EXAMPLE)
    configuration.load(p)
    for factory, target in ((connect, DB_PATH), (inbox_connect, inbox_path), (_connect, job_path)):
        with closing(factory(target)) as db:
            if db.execute("SELECT 1").fetchone() != (1,):
                raise ValueError("DATABASE_INITIALIZATION_CHECK_FAILED")
    return {**FLAGS, "status": "LOCAL_SETUP_COMPLETE", "config_created": created,
            "config": str(Path(path)), "database_initialization": "existing_schemas",
            "network_calls": 0, "exit_code": 0}


def _database_health(path):
    p = private_path(path)
    if not p.exists():
        return {"status": "NOT_INITIALIZED"}
    try:
        with closing(sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            deadline = time.monotonic() + 2
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            check = db.execute("PRAGMA quick_check(1)").fetchall()
            return {"status": "LOCAL_INTEGRITY_PASS" if check == [("ok",)] else "INTEGRITY_FAILURE",
                    "bytes": p.stat().st_size}
    except (OSError, sqlite3.Error):
        return {"status": "UNAVAILABLE_OR_CHECK_TIMEOUT"}


def doctor(config):
    from atlaspm.monitoring.api_management import inventory
    dependencies = {name: importlib.util.find_spec(name) is not None for name in
                    ("pandas_market_calendars", "numpy", "scipy", "duckdb", "pyarrow",
                     "sklearn", "lightgbm", "xgboost", "torch")}
    providers = inventory()
    from atlaspm.data.api_gateway import DEFAULT_GATEWAY, PROVIDERS
    represented = {row["provider"] for row in providers}
    for name, health in DEFAULT_GATEWAY.health().items():
        if name in represented:
            continue
        providers.append({"provider": name, "mode": "IMPLEMENTED_ADAPTER",
                          "credential_configured": health["credential_configured"],
                          "credential_env": "ATLASPM_SEC_USER_AGENT" if name.startswith("sec") else PROVIDERS[name].key_env,
                          "enabled_by_configuration": os.environ.get("ATLASPM_ENABLE_" + name.upper(), "1") == "1",
                          "connection_verified": False, "last_status": health["last_status"],
                          "ready_for_manual_attempt": False,
                          "effective_status": "ONLINE_ACCEPTANCE_REQUIRED"})
    # These licensed file-delivery adapters already exist; do not pretend a
    # proprietary wire endpoint is connected or that a configured file is licensed.
    delivery = {"yahoo_finance": "atlaspm.research.news_intelligence",
                "bloomberg": "atlaspm.research.news_intelligence",
                "investment_banks": "atlaspm.research.institutional_research"}
    for row in providers:
        if row["provider"] in delivery:
            row.update(mode="LICENSED_FILE_DELIVERY", delivery_adapter=delivery[row["provider"]],
                       effective_status="AUTHORIZED_DELIVERY_CONFIGURATION_REQUIRED",
                       ready_for_manual_attempt=False)
    database = _database_health(DB_PATH)
    blockers = []
    if database["status"] != "LOCAL_INTEGRITY_PASS":
        blockers.append("MARKET_DATABASE_SETUP_OR_REPAIR_REQUIRED")
    if config["sources"]["queue"] and not dependencies["pandas_market_calendars"]:
        blockers.append("CALENDAR_DEPENDENCY_REQUIRED")
    if config["local_ai"]["enabled"]:
        from atlaspm.research.alpha_universe import load_universe
        try:
            load_universe(config["local_ai"]["universe"])
        except (OSError, ValueError, KeyError, TypeError):
            blockers.append("REVIEWED_UNIVERSE_REQUIRED")
    return {**FLAGS, "status": "LOCAL_CONFIGURATION_CHECK_PASS" if not blockers else "LOCAL_CONFIGURATION_BLOCKED",
            "exit_code": int(bool(blockers)), "blockers": blockers, "dependencies": dependencies,
            "market_database": database, "providers": providers, "network_calls": 0,
            "local_model_connection_verified": False, "api_configuration_required": True,
            "live_data_certification_required": True, "model_validation_required": True,
            "paper_trading_validation_required": True}


def status():
    from atlaspm.framework.runtime_health import health
    result = {**FLAGS, "status": "NOT_STARTED", "exit_code": 0, "queue": health(),
              "heartbeat_is_process_liveness_proof": False}
    p = private_path(HEARTBEAT)
    if p.exists():
        with p.open("rb") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError("RUNTIME_HEARTBEAT_SIZE_LIMIT")
        snapshot = _strict_json(raw)
        age = (datetime.now(UTC) - _time(snapshot["updated_at"])).total_seconds()
        result.update(status=snapshot["status"], heartbeat=snapshot,
                      heartbeat_age_seconds=max(0., age),
                      heartbeat_stale=age < 0 or age > 300)
    return result


def _paused():
    c = event_queue._read_state(event_queue.PATH).get(event_queue.CONTROL_KEY, {})
    return c.get("stopped") or c.get("manual_takeover")


def _resources(config):
    from atlaspm.research.training_benchmark import peak_bytes
    from atlaspm.research.alpha_inbox import PATH as inbox
    from atlaspm.research.disclosure_jobs import PATH as jobs
    size = 0
    for path in (DB_PATH, inbox, jobs):
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = private_path(Path(str(path) + suffix))
            if p.exists():
                size += p.stat().st_size
    memory = peak_bytes()
    if memory > config["runtime"]["max_memory_mib"] * 1024**2:
        raise ValueError("RUNTIME_MEMORY_BUDGET_EXCEEDED")
    if size > config["runtime"]["max_database_mib"] * 1024**2:
        raise ValueError("RUNTIME_DATABASE_BUDGET_EXCEEDED")
    return {"process_lifetime_peak_rss_bytes": memory, "monitored_database_bytes": size}


def _tasks(config):
    from atlaspm.research.disclosure_monitor import poll_once
    from atlaspm.research.disclosure_jobs import work_once
    from atlaspm.framework.orchestrator import tick
    interval = config["runtime"]["interval_seconds"]
    tasks = {}
    for source in ("sec", "fed"):
        if config["sources"][source]:
            tasks[source] = (max(interval, 30 if source == "fed" else 1),
                             lambda s=source: poll_once(s, documents=config["sources"]["documents"]))
    if config["sources"]["documents"]:
        tasks["documents"] = (interval, work_once)
    if config["sources"]["queue"]:
        tasks["queue"] = (interval, lambda: tick(as_of=datetime.now(UTC).isoformat()))
    from atlaspm.research import alpha_sources as sources
    research = config["research"]
    def added(call):
        return {"status": "INGESTED", "added": call()}
    if research["treasury"]:
        tasks["treasury"] = (300, lambda: added(sources.treasury_once))
    if research["options"]:
        tasks["unusual_whales"] = (5, lambda: added(sources.options_once))
    for series in research["fred_series"]:
        tasks["fred:" + series] = (300, lambda s=series: added(lambda: sources.fred_once(s)))
    for provider in research["news_providers"]:
        def news(p=provider):
            now = datetime.now(UTC)
            return added(lambda: sources.news_once(p, now.strftime("%Y-%m"), as_of=now.isoformat()))
        tasks["news:" + provider] = (30, news)
    for index, request in enumerate(research["credit_requests"]):
        scope, kind, identifier, symbol = request.split(":")
        tasks["credit:" + str(index)] = (900, lambda a=scope, b=kind, c=identifier, d=symbol:
            added(lambda: sources.credit_once(scope=a, identifier_type=b, identifier=c, symbol=d)))
    for index, request in enumerate(research["institutional_requests"]):
        subject, symbol = request.split("=", 1)
        tasks["institutional:" + str(index)] = (300, lambda s=subject, t=symbol:
            added(lambda: sources.institutional_once(subject=s, symbol=t, as_of=datetime.now(UTC).isoformat())))
    if research["fundamental_universe"]:
        from collections import deque
        from atlaspm.research.alpha_universe import load_universe
        from atlaspm.research.alpha_fundamentals import once
        rows = load_universe(research["fundamental_universe"])["rows"]
        pending = deque((s, r["cik"]) for s, r in sorted(rows.items())
                        if r["active"] and r["asset_type"] == "stock" and r["cik"] and int(r["cik"]))
        if not pending:
            raise ValueError("RUNTIME_REVIEWED_COMPANY_IDENTITIES_REQUIRED")
        def fundamental():
            total = 0
            for _ in range(min(research["fundamental_batch"], len(pending))):
                if _paused():
                    return {"status": "PAUSED"}
                symbol, cik = pending[0]
                total += once(symbol=symbol, cik=cik)
                pending.rotate(-1)
            return {"status": "INGESTED", "added": total}
        tasks["fundamentals"] = (60, fundamental)
    if config["local_ai"]["enabled"]:
        from atlaspm.research.alpha_universe import load_universe, batches
        from atlaspm.research.alpha_inbox import run_once
        from itertools import cycle
        ai = config["local_ai"]
        groups = list(batches(load_universe(ai["universe"]), size=ai["batch_size"]))
        if not groups:
            raise ValueError("RUNTIME_ACTIVE_UNIVERSE_REQUIRED")
        groups = cycle(groups)
        tasks["local_ai"] = (interval, lambda: run_once(universe=next(groups), model=ai["model"]))
    return tasks


def monitor(config, *, allow_network=False, allow_local_ai=False, clock=time.monotonic,
            sleep=time.sleep, tasks=None, resource_check=_resources):
    config = configuration.validate(config)
    network = any(config["sources"][k] for k in ("sec", "fed", "documents")) or any(
        config["research"][k] for k in ("treasury", "options", "fred_series", "credit_requests", "fundamental_universe"))
    if network and allow_network is not True:
        raise ValueError("RUNTIME_EXPLICIT_NETWORK_OPTIN_REQUIRED")
    if config["local_ai"]["enabled"] and allow_local_ai is not True:
        raise ValueError("RUNTIME_EXPLICIT_LOCAL_AI_OPTIN_REQUIRED")
    tasks = _tasks(config) if tasks is None else tasks
    if not tasks:
        raise ValueError("RUNTIME_NO_ENABLED_TASKS")
    with WorkerLease():
        root = private_path(ROOT)
        logger = logging.Logger("atlaspm.system." + uuid4().hex)
        handler = RotatingFileHandler(root / "runtime.jsonl",
            maxBytes=config["runtime"]["max_log_mib"] * 1024**2,
            backupCount=config["runtime"]["log_backups"], encoding="utf-8")
        logger.addHandler(handler)
        run_id = uuid4().hex
        due, failures, reports = dict.fromkeys(tasks, 0.), dict.fromkeys(tasks, 0), {}
        state, completed, resources = "RUNNING", 0, {}

        def record():
            report = {**FLAGS, "status": state, "run_id": run_id, "pid": os.getpid(),
                      "updated_at": datetime.now(UTC).isoformat(), "cycles_completed": completed,
                      "services": reports, "resources": resources, "max_concurrency": 1,
                      "network_enabled": network, "local_ai_enabled": config["local_ai"]["enabled"],
                      "resource_limit_scope": "between_calls_not_os_enforced"}
            _atomic_write(private_path(HEARTBEAT), lambda h: h.write(json.dumps(report, allow_nan=False).encode()))
            logger.info(json.dumps(report, allow_nan=False))
            return report

        try:
            for iteration in range(config["runtime"]["cycles"]):
                if _paused():
                    state = "PAUSED"
                    break
                resources = resource_check(config)
                for name, (interval, call) in tasks.items():
                    if _paused():
                        state = "PAUSED"
                        break
                    if clock() < due[name]:
                        continue
                    try:
                        response = call()
                        if not isinstance(response, dict):
                            raise ValueError("SERVICE_RESPONSE_SCHEMA")
                        outcome = response.get("status", response.get("review", {}).get("status", "COMPLETED"))
                        import re
                        if not isinstance(outcome, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", outcome):
                            raise ValueError("SERVICE_STATUS_SCHEMA")
                        if outcome in {"FAILED", "BACKOFF", "CONTEXT_CAPACITY_BLOCKED"} or outcome.startswith("BLOCKED"):
                            raise ValueError("SERVICE_BLOCKED")
                    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                        failures[name] += 1
                        due[name] = clock() + min(3600, max(interval, 5) * 2**min(failures[name], 8))
                        reports[name] = {"status": "BACKOFF", "error_type": type(exc).__name__,
                                         "consecutive_failures": failures[name]}
                    else:
                        failures[name] = 0
                        due[name] = clock() + interval
                        reports[name] = {"status": outcome, "consecutive_failures": 0}
                    resources = resource_check(config)
                    record()
                completed = iteration + 1
                if state == "PAUSED":
                    break
                if iteration + 1 < config["runtime"]["cycles"]:
                    until = clock() + config["runtime"]["interval_seconds"]
                    while clock() < until and not _paused():
                        sleep(min(1., until - clock()))
            if state == "RUNNING":
                state = "DEGRADED" if any(failures.values()) else "COMPLETED"
        except KeyboardInterrupt:
            state = "STOPPED_BY_OPERATOR"
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            state = "RESOURCE_OR_STATE_BLOCKED"
            reports["runtime"] = {"status": "BLOCKED", "error_type": type(exc).__name__}
        finally:
            try:
                result = record()
            finally:
                handler.close()
                logger.removeHandler(handler)
        return {**result, "exit_code": int(state in {"DEGRADED", "RESOURCE_OR_STATE_BLOCKED"})}


def recover():
    from atlaspm.research.disclosure_jobs import recover as recover_jobs
    with WorkerLease():
        result = recover_jobs()
    return {**FLAGS, "status": "LOCAL_JOB_RECOVERY_REVIEWED", "disclosure_jobs": result,
            "orders_replayed": False, "automatic_resume": False, "exit_code": 0}
