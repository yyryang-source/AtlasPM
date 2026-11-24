"""Local immutable alpha-version audit trail; no paper or live authorization.

Reuse the training registry's content-addressed evidence. Hashes detect
accidental corruption, not authenticity against a malicious local writer.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3

from atlaspm.research.training_registry import canonical, digest, load, local_path, FLAGS
from atlaspm.research.alpha_metrics import eligible

TRANSITIONS = {
    "RESEARCH": {"VALIDATING"},
    "VALIDATING": {"VALIDATED","SUSPENDED"},
    "VALIDATED": {"SUSPENDED"},
    "SUSPENDED": {"RETIRED"},
    "RETIRED": set(),
}


def _connect(path):
    con = sqlite3.connect(local_path(path), timeout=30)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS alpha_events (
            version TEXT NOT NULL, sequence INTEGER NOT NULL,
            event_json TEXT NOT NULL, event_sha TEXT NOT NULL,
            PRIMARY KEY(version,sequence)) WITHOUT ROWID""")
    except BaseException:
        con.close()
        raise
    return con


def _history(con,version):
    previous = None
    events = []
    for sequence, raw, sha in con.execute(
            "SELECT sequence,event_json,event_sha FROM alpha_events WHERE version=? ORDER BY sequence",(version,)):
        event = json.loads(raw)
        state = events[-1]["to"] if events else None
        if (sequence!=len(events)+1 or event["sequence"]!=sequence or event["version"]!=version
                or event["previous_sha"]!=previous or digest(event)!=sha
                or event["from"]!=state
                or event["to"] not in (TRANSITIONS[state] if state else {"RESEARCH"})
                or (events and event["run_id"]!=events[0]["run_id"])):
            raise ValueError("ALPHA_LIFECYCLE_INTEGRITY_FAILURE")
        events.append({**event,"event_sha":sha})
        previous = sha
    return events


def _append(con, version, events, target, run_id, reason):
    state = events[-1]["to"] if events else None
    if target not in (TRANSITIONS[state] if state else {"RESEARCH"}):
        raise ValueError("ALPHA_ILLEGAL_TRANSITION")
    event = dict(version=version,sequence=len(events)+1,
                 previous_sha=events[-1]["event_sha"] if events else None,
                 run_id=run_id,reason=reason,created_at=datetime.now(timezone.utc).isoformat(),
                 **{"from":state,"to":target})
    sha = digest(event)
    con.execute("INSERT INTO alpha_events VALUES (?,?,?,?)",
                (version,event["sequence"],canonical(event),sha))
    events.append({**event,"event_sha":sha})


def track_run(path,run_id):
    """Register only research outcomes backed by existing registry evidence."""
    path=local_path(path)
    evidence=load(path,run_id)
    report=evidence["report"]
    version=report.get("strategy_version")
    if report.get("version")!="autonomous_alpha_v1" or not version or not report.get("selected_strategy"):
        raise ValueError("ALPHA_VERSION_EVIDENCE_REQUIRED")
    config=report["config"]
    rule=dict(minimum_sessions=config["minimum_sessions"],max_drawdown=config["max_drawdown"])
    candidate=report["selected_strategy"]
    accepted=(eligible(report["validation"][candidate],**rule)
              and report["holdout_candidates_evaluated"]==[candidate]
              and report["holdout"] is not None and eligible(report["holdout"],**rule))
    target="VALIDATED" if accepted else "SUSPENDED"
    if report["strategy_status"]!=target:
        raise ValueError("ALPHA_VALIDATION_EVIDENCE_MISMATCH")
    with closing(_connect(path)) as con,con:
        con.execute("BEGIN IMMEDIATE")
        events=_history(con,version)
        if events:
            if events[0]["run_id"]!=run_id:
                raise ValueError("ALPHA_VERSION_EVIDENCE_CONFLICT")
        else:
            for state,reason in (("RESEARCH","registered_research"),("VALIDATING","locked_candidate"),
                                 (target,"synthetic_holdout_verdict")):
                _append(con,version,events,state,run_id,reason)
    return events


def history(path,version):
    path=local_path(path)
    with closing(_connect(path)) as con:
        events=_history(con,version)
    if not events:
        raise ValueError("ALPHA_UNKNOWN_VERSION")
    evidence=load(path,events[0]["run_id"])
    if evidence["report"].get("strategy_version")!=version:
        raise ValueError("ALPHA_VERSION_EVIDENCE_MISMATCH")
    return dict(version=version,state=events[-1]["to"],events=events,
                paper_eligible=False,**FLAGS)


def transition(path,version,target,reason):
    """Manual de-risking only. Revalidation requires a new research version."""
    if target not in ("SUSPENDED","RETIRED") or not isinstance(reason,str) or not reason.strip():
        raise ValueError("ALPHA_ONLY_DE_RISK_TRANSITIONS_ALLOWED")
    path=local_path(path)
    history(path,version)
    with closing(_connect(path)) as con,con:
        con.execute("BEGIN IMMEDIATE")
        events=_history(con,version)
        _append(con,version,events,target,events[0]["run_id"],reason.strip())
    return history(path,version)


def compare_versions(path, champion, challenger):
    """Read-only comparison of already registered research versions.

    No approval based on a caller-provided report. Same dataset, holdout
    boundary and risk/evaluation configuration required. Never promotes or
    reactivates a suspended version; rollback requires new validation.
    """
    if champion==challenger:
        raise ValueError("DISTINCT_ALPHA_VERSIONS_REQUIRED")
    records=[]
    for version in (champion,challenger):
        life=history(path,version)
        evidence=load(path,life["events"][0]["run_id"])
        report=evidence["report"]
        records.append((life,evidence,report))
    left,right=records
    # Seed/model hyperparameters may differ (otherwise deterministic versions
    # cannot be compared). Evaluation dates, cost and risk policy must match.
    policy_keys=("validation_start","holdout_start","cost_bps","minimum_sessions","max_drawdown","sequence_length")
    policies=[{k:report["config"][k] for k in policy_keys} for _,_,report in records]
    comparable=(left[2]["dataset_sha256"]==right[2]["dataset_sha256"]
                and policies[0]==policies[1]
                and all(report["holdout"] is not None and report["holdout_mode"]=="locked_candidate_walk_forward_refit"
                        for _,_,report in records))
    return {**FLAGS,"comparable":comparable,"evaluation_policies":policies,
            "holdout_reuse_for_model_selection_allowed":False,
            "versions":[{"version":life["version"],"state":life["state"],
                         "registry_run_id":ev["run_id"],"report_sha256":ev["report_sha256"],
                         "holdout":report["holdout"] if comparable else None}
                        for life,ev,report in records],
            "independent_review_required":True,"rollback_requires_new_validation":True,
            "automatic_promotion_enabled":False,"paper_eligible":False}
