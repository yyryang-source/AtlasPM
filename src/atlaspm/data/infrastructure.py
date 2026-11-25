"""Read-only financial data infrastructure inventory and exact reference lookup.

No calls to providers, credentials, archive mutations, training or orders.
Catalog availability is structural, not completeness or source certification.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from atlaspm.data import unified
from atlaspm.data.market_db import DB_PATH, private_path
from atlaspm.monitoring.api_management import inventory

# Existing designated adapters are the source of truth for data processing.
# This inventory only exposes their route and known limitations.
ROUTES = (
    ("equity_realtime","atlaspm.execution.ibkr_market_data","IBKR_L1_EXPLICIT_SESSION"),
    ("macro","atlaspm.data.fred_observations","FRED_DATE_VINTAGE_EXPLICIT_REQUEST"),
    ("issuer_news","atlaspm.research.news_intelligence","LICENSED_METADATA_IMPORT"),
    ("global_news","atlaspm.research.news_intelligence","LICENSED_METADATA_IMPORT"),
    ("options_flow","atlaspm.research.options_intelligence","DESIGNATED_FEED_REVIEW"),
    ("credit_ratings","atlaspm.research.credit_intelligence","DESIGNATED_FEED_REVIEW"),
    ("analyst_reports","atlaspm.research.institutional_research","LICENSED_DOCUMENT_IMPORT"),
    ("policy_announcements","atlaspm.data.fed_disclosures","PUBLIC_RSS_EXPLICIT_REQUEST"),
    ("sec_fundamentals","atlaspm.data.sec_download","PUBLIC_COMPANYFACTS_EXPLICIT_REQUEST"),
    ("historical_identity","atlaspm.data.hf_stock5m_identity_reference_review","LOCAL_EVIDENCE_REVIEW"),
)


def _sqlite_inventory(path):
    tables={}
    if not path.is_file():
        return "DATABASE_MISSING",tables
    try:
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as con:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            names={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            expected={s.relation for s in unified.CATALOG.values() if s.store=="sqlite"}
            expected.update(t for t,_ in unified.REFERENCE_CATALOG.values())
            for name in sorted(expected & names):
                # All identifiers originate in the fixed internal catalog.
                tables[name]=set(r[1] for r in con.execute(f"PRAGMA table_info({name})"))
        return "DATABASE_READABLE",tables
    except sqlite3.Error:
        return "DATABASE_READ_FAILED",{}


def inspect(*, database=DB_PATH, gateway=None):
    path=private_path(Path(database))
    state,tables=_sqlite_inventory(path)
    rows=[]
    for name,s in sorted(unified.CATALOG.items()):
        if s.store=="sqlite":
            fields=set(s.keys+s.fields+("source",))
            if s.timeframe:
                fields.add("timeframe")
            status=_table_status(state,tables,s.relation,fields)
        else:
            status="PARTITION_SCOPE_REQUIRES_EXPLICIT_READ"
        rows.append({"dataset":name,"store":s.store,"relation":s.relation,
                     "access":"date_range_batches","status":status,
                     "coverage_verified":False,"point_in_time_verified":False})
    for name,(table,fields) in sorted(unified.REFERENCE_CATALOG.items()):
        rows.append({"dataset":name,"store":"sqlite","relation":table,
                     "access":"current_exact_identifier_lookup",
                     "status":_table_status(state,tables,table,set(fields)),
                     "coverage_verified":False,"point_in_time_verified":False})
    provider_rows=inventory() if gateway is None else inventory(gateway)
    return {"schema":"atlaspm_data_infrastructure_v1","local_only":True,
            "database_status":state,"datasets":rows,
            "specialized_routes":[{"dataset":n,"module":m,"access":a,
                                   "connection_verified":False} for n,m,a in ROUTES],
            "providers":provider_rows,
            "research_only":True,"approved_for_model_training":False,
            "approved_for_backtest":False,"live_execution_enabled":False,
            "broker_order_submission_enabled":False,"network_requests":0,
            "warning":"Table schema presence is not row coverage. Configuration and process health are not independent entitlement or connection verification."}


def _table_status(state,tables,relation,fields):
    if state!="DATABASE_READABLE":
        return state
    if relation not in tables:
        return "TABLE_MISSING"
    if not fields<=tables[relation]:
        return "TABLE_SCHEMA_MISMATCH"
    return "TABLE_SCHEMA_PRESENT"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_subparsers(dest="action",required=True)
    status=actions.add_parser("status")
    reference=actions.add_parser("reference")
    reference.add_argument("--dataset",choices=sorted(unified.REFERENCE_CATALOG),required=True)
    reference.add_argument("--identifier",action="append",required=True)
    for p in (status,reference):
        p.add_argument("--database",type=Path,default=DB_PATH)
    args=parser.parse_args()
    result=(inspect(database=args.database) if args.action=="status" else
            unified.read_reference(args.dataset,identifiers=args.identifier,database=args.database))
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=="__main__":
    main()
