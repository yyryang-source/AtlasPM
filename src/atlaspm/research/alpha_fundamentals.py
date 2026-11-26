"""Current SEC reported accounting facts for the single local alpha context.

No TTM synthesis, valuation inference, historical PIT certification or orders.
"""
from datetime import date, timedelta
from math import isfinite
import json

from atlaspm.data.api_gateway import DEFAULT_GATEWAY, GatewayError
from atlaspm.research.alpha_inbox import ingest
from atlaspm.research.financial_events import _digest, _time

TAGS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax":"USD",
    "Revenues":"USD", "SalesRevenueNet":"USD",
    "NetIncomeLoss":"USD", "OperatingIncomeLoss":"USD",
    "NetCashProvidedByUsedInOperatingActivities":"USD",
    "PaymentsToAcquirePropertyPlantAndEquipment":"USD",
    "Assets":"USD", "Liabilities":"USD", "StockholdersEquity":"USD",
    "CashAndCashEquivalentsAtCarryingValue":"USD",
    "LongTermDebtNoncurrent":"USD", "EarningsPerShareDiluted":"USD/shares",
}
STOCKS = {"Assets","Liabilities","StockholdersEquity",
          "CashAndCashEquivalentsAtCarryingValue","LongTermDebtNoncurrent"}


def extract(data, *, cik, as_of):
    cutoff = _time(as_of).date()
    if str(data.get("cik","")).zfill(10) != cik:
        raise GatewayError("SEC accounting identity mismatch")
    gaap = data["facts"]["us-gaap"]
    if not isinstance(gaap,dict):
        raise GatewayError("Invalid SEC taxonomy")
    result = {}
    for tag,unit in TAGS.items():
        rows = gaap.get(tag,{}).get("units",{}).get(unit,[])
        if not isinstance(rows,list) or len(rows)>20000:
            raise GatewayError("Invalid SEC fact rows")
        selected = {}
        for row in rows:
            if row.get("form") not in {"10-K","10-Q","10-K/A","10-Q/A"}:
                continue
            filed,end = date.fromisoformat(row["filed"]),date.fromisoformat(row["end"])
            # Date-only filing evidence cannot establish same-day availability.
            if not end<=filed<cutoff or cutoff-end>timedelta(days=550):
                continue
            value=row["val"]
            if type(value) not in {float,int} or not isfinite(value):
                raise GatewayError("Invalid SEC fact value")
            start=row.get("start")
            if tag in STOCKS:
                if start is not None:
                    continue
                bucket="instant"
            else:
                if start is None:
                    continue
                days=(end-date.fromisoformat(start)).days
                bucket="quarter" if 70<=days<=110 else "annual" if 350<=days<=380 else None
                if bucket is None:
                    continue
            item={"value":value,"unit":unit,"start":start,"end":row["end"],
                  "filed":row["filed"],"form":row["form"],"accession":row.get("accn")}
            key=(end,filed)
            prior=selected.get(bucket)
            if prior is None or key>prior[0]:
                selected[bucket]=(key,item)
            elif key==prior[0] and item["value"]!=prior[1]["value"]:
                raise GatewayError("Ambiguous SEC reported fact")
        if selected:
            result[tag]={k:v[1] for k,v in selected.items()}
    return result


def once(*, symbol, cik, gateway=DEFAULT_GATEWAY, writer=ingest):
    import re
    if not isinstance(cik,str) or not re.fullmatch(r"[0-9]{10}",cik) or int(cik)==0:
        raise ValueError("Reviewed nonzero CIK required")
    response=gateway.fetch("sec",f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    facts=extract(response.data(),cik=cik,as_of=response.fetched_at)
    if not facts:
        return 0
    evidence={"kind":"reported_accounting_facts","cik":cik,"facts":facts,
              "publication_time_verified":False,"valuation_computed":False}
    return writer(provider="sec",text=json.dumps(evidence,sort_keys=True,allow_nan=False),
                  symbols=[symbol],received_at=response.fetched_at,ttl=2592000,
                  source_revision=_digest([symbol,evidence]))
