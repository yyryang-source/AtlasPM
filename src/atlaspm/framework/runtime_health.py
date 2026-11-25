"""Read-only queue health and explicitly paused, isolated recovery copies.

No network, broker, model training, automatic resume or active queue restore.
Local hashes detect corruption, not malicious writers or source authenticity.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from collections import Counter

from atlaspm.data.market_db import private_path
from atlaspm.research import event_queue as queue
from atlaspm.research.financial_events import _time
from atlaspm.research.training_registry import FLAGS, digest

ROOT=Path("local_data/runs/recovery")
SCHEMA="atlaspm_runtime_health_v1"


def _validate(items):
    control=items.get(queue.CONTROL_KEY,{})
    if not isinstance(control,dict) or any(type(control.get(k,False)) is not bool
                                           for k in ("stopped","manual_takeover")):
        raise ValueError("QUEUE_CONTROL_SCHEMA")
    for key,task in items.items():
        if key==queue.CONTROL_KEY:
            continue
        if (not isinstance(task,dict) or task.get("state") not in
                ("pending","completed","failed","superseded")
                or type(task.get("attempts")) is not int or not 0<=task["attempts"]<=3):
            raise ValueError("QUEUE_TASK_SCHEMA")
        event=task["event"]
        if key!=digest([event["event_id"],event["revision"]]):
            raise ValueError("QUEUE_TASK_IDENTITY")
        _time(event["received_at"])
        if task["state"]=="completed" and not isinstance(task.get("report"),dict):
            raise ValueError("QUEUE_COMPLETED_REPORT_REQUIRED")
    return control


def health(*,path=queue.PATH,as_of=None,stale_seconds=300):
    """One bounded state observation; never creates state, starts work or clears locks."""
    if type(stale_seconds) is not int or not 60<=stale_seconds<=86400:
        raise ValueError("RUNTIME_HEALTH_STALE_BUDGET")
    now=datetime.now(UTC) if as_of is None else _time(as_of)
    p=private_path(Path(path))
    alerts=[]
    counts=Counter()
    visible_pending=0
    future_pending=0
    last_age=None
    oldest_age=None
    lock=p.with_suffix(".lock")
    paused=None
    exists=p.is_file()
    revision=None
    if lock.exists():
        alerts.append("QUEUE_WRITER_LOCK_PRESENT")
    if not exists:
        alerts.append("QUEUE_NOT_INITIALIZED")
    else:
        try:
            items,revision=queue._read_state_with_hash(p)
            control=_validate(items)
            paused=bool(control.get("stopped") or control.get("manual_takeover"))
            if paused:
                alerts.append("QUEUE_OPERATOR_PAUSED")
            ages=[]
            for key,task in items.items():
                if key==queue.CONTROL_KEY:
                    continue
                state=task["state"]
                counts[state]+=1
                if state=="pending":
                    age=(now-_time(task["event"]["received_at"])).total_seconds()
                    if age>=0:
                        visible_pending+=1
                        ages.append(age)
                    else:
                        future_pending+=1
            oldest_age=max(ages,default=None)
            if counts["failed"]:
                alerts.append("QUEUE_QUARANTINED_TASKS")
            if oldest_age is not None and oldest_age>stale_seconds and not paused:
                alerts.append("QUEUE_PENDING_AGE_EXCEEDED")
            last=control.get("last_as_of")
            if last is None:
                alerts.append("QUEUE_NO_TICK_EVIDENCE")
            else:
                last_age=(now-_time(last)).total_seconds()
                if last_age<0:
                    alerts.append("QUEUE_TICK_AFTER_OBSERVATION")
                elif last_age>stale_seconds and not paused:
                    alerts.append("QUEUE_TICK_STALE")
        except (OSError,ValueError,TypeError,KeyError,RecursionError):
            alerts.append("QUEUE_INTEGRITY_OR_READ_FAILURE")
            counts.clear()
            visible_pending=future_pending=0
            oldest_age=last_age=None
            paused=None
            revision=None
    return {"schema":SCHEMA,**FLAGS,"as_of":now.isoformat(),"queue_exists":exists,
            "queue_revision_sha256":revision,"operator_paused":paused,
            "queue_counts":dict(counts),"visible_pending":visible_pending,
            "future_received_pending":future_pending,"oldest_visible_pending_age_seconds":oldest_age,
            "last_tick_age_seconds":last_age,"stale_seconds":stale_seconds,
            "alerts":alerts,"status":"REVIEW_REQUIRED" if alerts else "LOCAL_QUEUE_CHECK_PASS",
            "provider_connections_verified":False,"broker_connection_verified":False,
            "production_ready":False,"automatic_recovery_enabled":False}


def _recovery_path(identifier):
    if (not isinstance(identifier,str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}",identifier)
            or identifier.upper() in {"CON","PRN","AUX","NUL",
                                      *(f"COM{i}" for i in range(1,10)),
                                      *(f"LPT{i}" for i in range(1,10))}):
        raise ValueError("RECOVERY_ID")
    root=private_path(ROOT)
    p=private_path(ROOT/(identifier+".json"))
    # Recovery cannot alias an existing source or redirected file.
    if p.parent!=root or p.name!=identifier+".json" or (ROOT/(identifier+".json")).is_symlink():
        raise ValueError("RECOVERY_PATH")
    return p


def _paused_and_rights(items):
    control=_validate(items)
    if not (control.get("stopped") or control.get("manual_takeover")):
        raise ValueError("RECOVERY_REQUIRES_PAUSED_QUEUE")
    from atlaspm.data.provider_policy import RIGHTS_ENV,local_rights
    for provider in {v["event"]["provider"] for k,v in items.items() if k!=queue.CONTROL_KEY}:
        if provider in RIGHTS_ENV:
            local_rights(provider,retention=True)
    return control


def _copy_new(path,items):
    sha=digest(items)
    raw=json.dumps({"items":items,"sha256":sha},sort_keys=True,
                   ensure_ascii=False,allow_nan=False).encode()
    if len(raw)>queue.MAX_BYTES:
        raise ValueError("RECOVERY_BYTE_BUDGET")
    path.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation; a crash can leave an incomplete file but verification
    # will reject it. Never overwrite previous evidence or live queue state.
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    copied,check=queue._read_state_with_hash(path)
    if check!=sha or copied!=items:
        raise ValueError("RECOVERY_COPY_VERIFICATION")
    return {"schema":"atlaspm_paused_queue_recovery_v1",**FLAGS,
            "copy_path":str(path.relative_to(Path.cwd().resolve())),
            "sha256":sha,"item_count":len(items)-int(queue.CONTROL_KEY in items),
            "paused":True,"active_queue_changed":False,"automatic_resume":False,
            "production_recovery_verified":False}


def backup(identifier,*,path=queue.PATH):
    source=private_path(Path(path),exists=True)
    dest=_recovery_path(identifier)
    if source==dest:
        raise ValueError("RECOVERY_SOURCE_ALIAS")
    def capture(items):
        _paused_and_rights(items)
        return _copy_new(dest,items)
    # Reuse the queue's exclusive lock and validated snapshot. The callback
    # never mutates source items, so the existing state is not rewritten.
    return queue._transaction(source,capture)


def verify_copy(identifier):
    p=_recovery_path(identifier)
    items,sha=queue._read_state_with_hash(p)
    if sha is None:
        raise ValueError("RECOVERY_COPY_MISSING")
    _paused_and_rights(items)
    return {"schema":"atlaspm_paused_queue_recovery_v1",**FLAGS,
            "sha256":sha,"paused":True,"item_count":len(items)-int(queue.CONTROL_KEY in items),
            "active_queue_changed":False,"automatic_resume":False}


def restore_isolated(identifier,destination):
    source=_recovery_path(identifier)
    target=_recovery_path(destination)
    if source==target:
        raise ValueError("RECOVERY_SOURCE_ALIAS")
    items,sha=queue._read_state_with_hash(source)
    if sha is None:
        raise ValueError("RECOVERY_COPY_MISSING")
    _paused_and_rights(items)
    return _copy_new(target,items)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    h=sub.add_parser("health")
    h.add_argument("--stale-seconds",type=int,default=300)
    b=sub.add_parser("backup")
    b.add_argument("identifier")
    v=sub.add_parser("verify")
    v.add_argument("identifier")
    r=sub.add_parser("restore-isolated")
    r.add_argument("identifier")
    r.add_argument("destination")
    a=p.parse_args()
    if a.action=="health":
        result=health(stale_seconds=a.stale_seconds)
    elif a.action=="backup":
        result=backup(a.identifier)
    elif a.action=="verify":
        result=verify_copy(a.identifier)
    else:
        result=restore_isolated(a.identifier,a.destination)
    print(json.dumps(result,indent=2,allow_nan=False))
    return int(result.get("status")=="REVIEW_REQUIRED")


if __name__=="__main__":
    raise SystemExit(main())
