"""One-use matured synthetic mark-to-market feedback from reconciled Paper OMS.

No cash settlement is assumed: receivables/payables remain part of equity.
Margin carrying cost is an explicit approximation, not a broker calculation.
"""
from __future__ import annotations

from contextlib import closing
from datetime import timezone
import json
from atlaspm.engine.contracts import finite,timestamp
from atlaspm.research.training_registry import FLAGS, local_path, load, _connect, canonical, digest


def _table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS paper_feedback (
      decision_id TEXT PRIMARY KEY, research_run_id TEXT NOT NULL,
      account_id TEXT NOT NULL,maturity_date TEXT NOT NULL,available_at TEXT NOT NULL,
      report_json TEXT NOT NULL,report_sha256 TEXT NOT NULL,
      UNIQUE(research_run_id,account_id,maturity_date))""")


def record_paper_feedback(*,registry_path,decision_id,observation,as_of):
    path=local_path(registry_path)
    record=load(path,decision_id)
    report,config=record["report"],record["config"]
    if report.get("status")!="SYNTHETIC_PAPER_DECISION" or report.get("decision")!="SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE":
        raise ValueError("RECONCILED_PAPER_DECISION_REQUIRED")
    if observation.get("mode")!="synthetic" or observation.get("synthetic_fixture") is not True:
        raise ValueError("SYNTHETIC_FEEDBACK_ONLY")
    if any(k in observation and observation[k] is not v for k,v in FLAGS.items()):
        raise ValueError("FEEDBACK_PERMISSION_OVERRIDE_DENIED")
    observed=timestamp(observation["observed_at"],"observed_at")
    available=timestamp(observation["available_at"],"available_at")
    now=timestamp(as_of,"as_of")
    decision_time=timestamp(config["as_of"],"decision_as_of")
    if not decision_time<observed<=available<=now or observed.date().isoformat()!=config["maturity_date"]:
        raise ValueError("FEEDBACK_NOT_MATURED_OR_WRONG_SESSION")
    portfolio=report["portfolio"]
    checks=portfolio["reconciliation_checks"]
    if not checks or not all(v is True for v in checks.values()):
        raise ValueError("PAPER_RECONCILIATION_REQUIRED")
    account=portfolio["paper_oms"]["account_replay"]
    positions=account["positions"]
    if any(p["type"]!="equity" for p in positions):
        raise ValueError("PAPER_FEEDBACK_EQUITY_ONLY")
    marks=observation["marks"]
    if set(marks)!={p["identifier"] for p in positions}:
        raise ValueError("FEEDBACK_MARK_COVERAGE_MISMATCH")
    market_value=sum(p["shares"]*finite(marks[p["identifier"]],"matured_mark",minimum=.000001)
                     for p in positions)
    financing=account["margin_debt"]*config["annual_margin_rate"]*(observed-decision_time).total_seconds()/86400/365
    ending=market_value+account["settled_cash"]+account["unsettled_receivable"]-account["unsettled_payable"]-account["margin_debt"]-account["accrued_financing_interest"]-financing
    opening=finite(account["opening_net_liquidation"],"opening_equity",minimum=.000001)
    net=finite(ending/opening-1,"matured_net_return")
    result=dict(decision_id=decision_id,research_run_id=config["research_run_id"],
                account_id=config["account_id"],maturity_date=config["maturity_date"],
                available_at=available.astimezone(timezone.utc).isoformat(),
                observed_at=observation["observed_at"],marks=marks,net_return=net,
                estimated_financing_cost=financing,ending_equity=ending,
                synthetic_mark_to_market_only=True,**FLAGS)
    with closing(_connect(path)) as con,con:
        con.execute("BEGIN IMMEDIATE")
        _table(con)
        old=con.execute("SELECT report_sha256 FROM paper_feedback WHERE decision_id=? OR (research_run_id=? AND account_id=? AND maturity_date=?)",
                        (decision_id,config["research_run_id"],config["account_id"],config["maturity_date"])).fetchone()
        if old:
            if old[0]!=digest(result):
                raise ValueError("PAPER_FEEDBACK_ALREADY_FINALIZED")
        else:
            con.execute("INSERT INTO paper_feedback VALUES (?,?,?,?,?,?,?)",
                        (decision_id,config["research_run_id"],config["account_id"],
                         config["maturity_date"],result["available_at"],canonical(result),digest(result)))
    return result


def matured_feedback(registry_path,research_run_id,account_id,as_of):
    path=local_path(registry_path)
    with closing(_connect(path)) as con,con:
        _table(con)
        rows=con.execute("""SELECT report_json,report_sha256 FROM paper_feedback
            WHERE research_run_id=? AND account_id=? AND maturity_date<? AND available_at<?
            ORDER BY maturity_date DESC LIMIT 5""",(research_run_id,account_id,
            as_of.date().isoformat(),as_of.astimezone(timezone.utc).isoformat())).fetchall()
    result=[]
    for raw,sha in reversed(rows):
        item=json.loads(raw)
        if digest(item)!=sha or any(item.get(k) is not v for k,v in FLAGS.items()):
            raise ValueError("PAPER_FEEDBACK_INTEGRITY_FAILURE")
        result.append(item)
    return result
