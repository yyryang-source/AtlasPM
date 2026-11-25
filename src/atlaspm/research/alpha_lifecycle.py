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
        con.execute("""CREATE TABLE IF NOT EXISTS alpha_review_events (
            request_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            event_json TEXT NOT NULL, event_sha TEXT NOT NULL,
            PRIMARY KEY(request_id,sequence)) WITHOUT ROWID""")
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


def _review_chain(con, request_id):
    events=[]
    for sequence,raw,sha in con.execute(
            "SELECT sequence,event_json,event_sha FROM alpha_review_events WHERE request_id=? ORDER BY sequence",
            (request_id,)):
        event=json.loads(raw)
        prior=events[-1] if events else None
        if (sequence!=len(events)+1 or event["sequence"]!=sequence or event["request_id"]!=request_id
                or event["previous_sha"]!=(prior["event_sha"] if prior else None) or digest(event)!=sha
                or (prior is None and (event["action"]!="REQUEST" or digest(event["binding"])!=request_id))
                or (prior is not None and (prior["action"],event["action"]) not in
                    {("REQUEST","APPROVE"),("REQUEST","REJECT"),("APPROVE","REVOKE")})):
            raise ValueError("ALPHA_REVIEW_INTEGRITY_FAILURE")
        events.append({**event,"event_sha":sha})
    return events


def _review_append(con, request_id, events, action, payload):
    event={**payload,"request_id":request_id,"action":action,"sequence":len(events)+1,
           "previous_sha":events[-1]["event_sha"] if events else None,
           "created_at":datetime.now(timezone.utc).isoformat()}
    sha=digest(event)
    con.execute("INSERT INTO alpha_review_events VALUES (?,?,?,?)",
                (request_id,event["sequence"],canonical(event),sha))
    return {**event,"event_sha":sha}


def _review_identity(value):
    if not isinstance(value,str) or not 1<=len(value)<=128 or not value.strip() or any(ord(c)<32 for c in value):
        raise ValueError("ALPHA_REVIEW_IDENTITY_REQUIRED")
    return value


def request_review(path, *, version, requester, purpose, predecessor=None):
    """Create a review-only request from existing immutable registry evidence.

    Rollback/revalidation always targets a NEW version with a different data
    fingerprint; that is necessary but not sufficient for independent data
    certification. No active model pointer or execution authority is created.
    """
    requester=_review_identity(requester)
    if purpose not in {"promotion","rollback","revalidation"}:
        raise ValueError("ALPHA_REVIEW_PURPOSE")
    life=history(path,version)
    if life["state"]!="VALIDATED":
        raise ValueError("ALPHA_REVIEW_REQUIRES_VALIDATED_VERSION")
    ev=load(path,life["events"][0]["run_id"])
    report=ev["report"]
    prior=None
    if purpose!="promotion":
        if not predecessor or predecessor==version:
            raise ValueError("ALPHA_REVALIDATION_REQUIRES_NEW_VERSION")
        old=history(path,predecessor)
        old_ev=load(path,old["events"][0]["run_id"])
        if old_ev["report"]["dataset_sha256"]==report["dataset_sha256"]:
            raise ValueError("ALPHA_REVALIDATION_REQUIRES_NEW_DATA_EVIDENCE")
        prior={"version":predecessor,"registry_run_id":old_ev["run_id"],
               "report_sha256":old_ev["report_sha256"]}
    elif predecessor is not None:
        raise ValueError("ALPHA_PROMOTION_PREDECESSOR_NOT_ALLOWED")
    binding={"version":version,"requester":requester,"purpose":purpose,"predecessor":prior,
             "registry_run_id":ev["run_id"],"validation_report_sha256":ev["report_sha256"],
             "dataset_sha256":report["dataset_sha256"],"strategy_id":report["strategy_id"],
             "evaluation_policy_sha256":digest(report["config"]),
             "lifecycle_event_sha256":life["events"][-1]["event_sha"]}
    request_id=digest(binding)
    with closing(_connect(path)) as con,con:
        con.execute("BEGIN IMMEDIATE")
        if _history(con,version)[-1]["event_sha"]!=binding["lifecycle_event_sha256"]:
            raise ValueError("ALPHA_REVIEW_LIFECYCLE_CHANGED")
        if not _review_chain(con,request_id):
            _review_append(con,request_id,[],"REQUEST",{"binding":binding})
    return review_status(path,request_id)


def review_status(path, request_id):
    with closing(_connect(path)) as con:
        events=_review_chain(con,request_id)
    if not events:
        raise ValueError("ALPHA_UNKNOWN_REVIEW_REQUEST")
    binding=events[0]["binding"]
    life=history(path,binding["version"])
    ev=load(path,binding["registry_run_id"])
    if (ev["report_sha256"]!=binding["validation_report_sha256"]
            or ev["report"]["dataset_sha256"]!=binding["dataset_sha256"]
            or ev["report"]["strategy_version"]!=binding["version"]
            or digest(ev["report"]["config"])!=binding["evaluation_policy_sha256"]):
        raise ValueError("ALPHA_REVIEW_EVIDENCE_CHANGED")
    current=life["state"]=="VALIDATED" and life["events"][-1]["event_sha"]==binding["lifecycle_event_sha256"]
    action=events[-1]["action"]
    return {**FLAGS,"request_id":request_id,"binding":binding,"events":events,
            "review_state":action if current else "INVALIDATED",
            "lifecycle_current":current,"formal_approval_enabled":False,
            "paper_eligible":False,"operational_model_active":False}


def decide_review(path, *, request_id, reviewer, decision, reason, signature):
    """Verify an externally produced operator HMAC on the exact review.

    The reviewer key must be kept in a separate trusted review process/OS
    account, not in the model worker. This is a local shared-secret workflow,
    not nonrepudiation or independently verified human identity. All outcomes
    remain review-only while real data/validation certification is disabled.
    """
    import hashlib,hmac,os,re
    reviewer=_review_identity(reviewer)
    reason=_review_identity(reason)
    if decision not in {"APPROVE","REJECT","REVOKE"}:
        raise ValueError("ALPHA_REVIEW_DECISION")
    configured=os.environ.get("ATLASPM_GOVERNANCE_REVIEWER")
    secret=os.environ.get("ATLASPM_GOVERNANCE_REVIEW_KEY","")
    if (reviewer!=configured or not re.fullmatch(r"[0-9a-f]{64}",secret)
            or not isinstance(signature,str) or not re.fullmatch(r"[0-9a-f]{64}",signature)):
        raise ValueError("ALPHA_INDEPENDENT_REVIEW_CREDENTIAL_REQUIRED")
    signed={"request_id":request_id,"reviewer":reviewer,"decision":decision,"reason":reason}
    expected=hmac.new(bytes.fromhex(secret),canonical(signed).encode("utf-8"),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,expected):
        raise ValueError("ALPHA_REVIEW_SIGNATURE_INVALID")
    info=review_status(path,request_id)
    if reviewer==info["binding"]["requester"]:
        raise ValueError("ALPHA_SELF_APPROVAL_FORBIDDEN")
    with closing(_connect(path)) as con,con:
        con.execute("BEGIN IMMEDIATE")
        events=_review_chain(con,request_id)
        prior=events[-1]["action"]
        if (prior,decision) not in {("REQUEST","APPROVE"),("REQUEST","REJECT"),("APPROVE","REVOKE")}:
            raise ValueError("ALPHA_REVIEW_TRANSITION")
        life=_history(con,info["binding"]["version"])
        if decision=="APPROVE" and (life[-1]["to"]!="VALIDATED" or
                life[-1]["event_sha"]!=info["binding"]["lifecycle_event_sha256"]):
            raise ValueError("ALPHA_REVIEW_LIFECYCLE_CHANGED")
        _review_append(con,request_id,events,decision,{"reviewer":reviewer,"reason":reason,
                       "signature_sha256":digest(signature),"authentication":"operator_hmac_verified"})
    return review_status(path,request_id)
