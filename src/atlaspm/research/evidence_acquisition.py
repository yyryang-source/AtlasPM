"""Plan bounded SEC evidence acquisition from diagnostic sample gaps.

The priority report is a local planning input, not an admission artifact.
Explicit issuer candidates come only from reviewed normalized references,
not inferred ticker history. No network, claim ingest or ledger mutations.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
import json
from pathlib import Path
import re

from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data import hf_stock5m_identity_reference_review as reference
from atlaspm.data import hf_stock5m_security_master_v53 as ledger
from atlaspm.data.market_db import private_path
from atlaspm.research.real_dataset_preflight_v61 import SCHEMA as PREFLIGHT_SCHEMA

MAX_BYTES=16*1024*1024
MONTH=re.compile(r"(20[0-9]{2})-(0[1-9]|1[0-2])")
COUNTS=("identity_gap_horizon_rows","visible_action_excluded_horizon_rows",
        "feature_history_gap_horizon_rows")


def _load_priority(filename):
    path=private_path(Path(filename),exists=True)
    with path.open("rb") as f:
        raw=f.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:
        raise ValueError("PRIORITY_REPORT_BYTE_BUDGET")
    report=json.loads(raw)
    if (not isinstance(report,dict) or report.get("schema")!=PREFLIGHT_SCHEMA
            or report.get("approved_for_model_training") is not False
            or not isinstance(report.get("dataset_id"),str)
            or not ledger.SHA.fullmatch(report["dataset_id"])):
        raise ValueError("EXPECTED_BLOCKED_DATASET_PREFLIGHT")
    queue=report.get("evidence_priority")
    if (not isinstance(queue,dict) or queue.get("schema")!="atlaspm_evidence_priority_v1"
            or queue.get("all_counts_reconciled") is not True
            or queue.get("claims_ingested")!=0):
        raise ValueError("EXPECTED_RECONCILED_PRIORITY_QUEUE")
    rows=queue.get("priority_rows")
    if (not isinstance(rows,list) or len(rows)>1000
            or queue.get("reported_symbols")!=len(rows)):
        raise ValueError("PRIORITY_QUEUE_ROW_BUDGET_OR_COUNT")
    seen=set()
    for row in rows:
        if (not isinstance(row,dict) or not isinstance(row.get("symbol"),str)
                or not ledger.SYMBOL.fullmatch(row["symbol"]) or row["symbol"] in seen):
            raise ValueError("INVALID_OR_DUPLICATE_PRIORITY_SYMBOL")
        seen.add(row["symbol"])
        if any(type(row.get(k)) is not int or not 0<=row[k]<=10**12 for k in COUNTS):
            raise ValueError("INVALID_PRIORITY_COUNT")
        months=row.get("affected_signal_months")
        if (not isinstance(months,list) or not 1<=len(months)<=12
                or any(not isinstance(m,str) or not MONTH.fullmatch(m) for m in months)
                or len(set(months))!=len(months)):
            raise ValueError("INVALID_PRIORITY_MONTH_SCOPE")
    return report,rows,ev._sha(raw)


def _month_span(month):
    y,m=map(int,month.split("-"))
    return month+"-01",date(y+1,1,1).isoformat() if m==12 else date(y,m+1,1).isoformat()


def plan(priority_file, *, year, as_of, reference_file=None, max_issuers=20):
    if type(year) is not int or not 2000<=year<=2026:
        raise ValueError("YEAR_OUTSIDE_SEC_INDEX_SCOPE")
    if type(max_issuers) is not int or not 1<=max_issuers<=100:
        raise ValueError("ISSUER_BUDGET_1_TO_100")
    as_of=ledger._date(as_of)
    report,rows,priority_sha=_load_priority(priority_file)
    refs,manifest=reference.load_reference(reference_file) if reference_file else ([],None)
    by_symbol=defaultdict(list)
    for r in refs:
        if r["known_date"]<as_of:
            by_symbol[r["symbol"]].append(r)
    chosen=set()
    items=[]
    # Recompute deterministic rank instead of trusting file ordering.
    ordered=sorted(rows,key=lambda r:(-r[COUNTS[0]],-r[COUNTS[2]],-r[COUNTS[1]],r["symbol"]))
    for row in ordered:
        months=sorted(m for m in row["affected_signal_months"] if m.startswith(str(year)+"-")
                      and m+"-01"<as_of)
        if not months:
            continue
        spans=[_month_span(m) for m in months]
        visible=[r for r in by_symbol[row["symbol"]]
                 if any(r["valid_from"]<min(end,as_of)
                        and (r["valid_to"] is None or r["valid_to"]>start)
                        for start,end in spans)]
        ciks=sorted({r["cik"] for r in visible})
        if row[COUNTS[0]]==0:
            status="NON_IDENTITY_GAP_REVIEW"
        elif not ciks:
            status="EXTERNAL_EXPLICIT_CIK_REQUIRED"
        elif len(chosen|set(ciks))>max_issuers:
            # Do not partially select competing issuers for a symbol.
            status="ISSUER_BUDGET_DEFERRED"
        else:
            chosen.update(ciks)
            status="MULTIPLE_CIKS_REQUIRE_REVIEW" if len(ciks)>1 else "EXPLICIT_CIK_CANDIDATE"
        items.append({"symbol":row["symbol"],"affected_signal_months":months,
                      **{k:row[k] for k in COUNTS},"status":status,
                      "declared_cik_candidates":ciks,
                      "reference_ids":sorted(r["reference_id"] for r in visible),
                      "security_id":None,"valid_from":None,"valid_to":None})
    return {"schema":"atlaspm_targeted_evidence_plan_v1","dataset_id":report["dataset_id"],
            "priority_report_sha256":priority_sha,"priority_scope_truncated":report["evidence_priority"].get("truncated"),
            "reference_manifest":manifest,"year":year,"as_of_exclusive":as_of,
            "max_issuers":max_issuers,"selected_ciks":sorted(chosen),"tasks":items,
            "network_requests":0,"claims_ingested":0,"certified_identities":0,
            "research_only":True,"independent_source_verified":False,
            "approved_for_model_training":False,"approved_for_backtest":False,
            "live_execution_enabled":False,"broker_order_submission_enabled":False,
            "warning":("Planning only. Full-dataset horizon-row counts may span other years; "
                       "not recoverable samples or year-specific counts. Declared reference "
                       "candidates are not authenticated identities. No symbol-to-CIK inference.")}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--priority-file",required=True)
    p.add_argument("--reference-file")
    p.add_argument("--year",type=int,required=True)
    p.add_argument("--as-of",required=True)
    p.add_argument("--max-issuers",type=int,default=20)
    a=p.parse_args()
    print(json.dumps(plan(a.priority_file,year=a.year,as_of=a.as_of,
                          reference_file=a.reference_file,max_issuers=a.max_issuers),
                     indent=2,allow_nan=False))


if __name__=="__main__":
    main()
