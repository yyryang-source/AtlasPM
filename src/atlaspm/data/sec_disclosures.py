"""Official SEC submissions JSON through the existing Gateway.

Current recent filings only. Acceptance timestamp is provider metadata;
not independently proven first publication time. No full archive download.
"""
from __future__ import annotations
from datetime import UTC, datetime
import re
from atlaspm.data.api_gateway import DEFAULT_GATEWAY, GatewayError
from atlaspm.research.financial_events import normalize, _digest

FORMS=frozenset({"8-K","8-K/A","6-K","6-K/A","10-K","10-K/A","10-Q","10-Q/A",
                 "20-F","40-F","25","25-NSE","S-1","S-3","SC 13D","SC 13G"})


def recent_events(cik, *, gateway=DEFAULT_GATEWAY):
    if not isinstance(cik,str) or not re.fullmatch(r"[0-9]{1,10}",cik) or int(cik)==0:
        raise ValueError("Expected SEC CIK")
    cik=cik.zfill(10)
    response=gateway.fetch("sec","https://data.sec.gov/submissions/CIK"+cik+".json")
    body=response.data()
    if str(body.get("cik","")).zfill(10)!=cik:
        raise GatewayError("SEC submission identity mismatch")
    recent=body.get("filings",{}).get("recent",{})
    keys=("accessionNumber","form","acceptanceDateTime","filingDate")
    cols=[recent.get(k) for k in keys]
    if any(not isinstance(c,list) or len(c)>5000 for c in cols) or len({len(c) for c in cols})!=1:
        raise GatewayError("SEC filing columns mismatch")
    events=[]
    filings=[]
    for accession,form,accepted,filing in zip(*cols):
        if form not in FORMS:
            continue
        if not isinstance(accession,str) or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}",accession):
            raise GatewayError("Invalid SEC accession")
        try:
            stamp=datetime.fromisoformat(accepted)
        except (TypeError,ValueError):
            raise GatewayError("Invalid SEC acceptance timestamp") from None
        # A naive acceptance timestamp is not guessed to be UTC.
        accepted_utc=stamp.astimezone(UTC).isoformat() if stamp.utcoffset() is not None else None
        filing_meta={"cik":cik,"accession":accession,"form":form,"accepted_raw":accepted,
                     "accepted_utc":accepted_utc,"filing_date":filing}
        digest=_digest(filing_meta)
        filings.append({**filing_meta,"evidence_sha256":digest})
        events.append(normalize({"provider":"sec","source_id":accession,"kind":"sec_disclosure",
            "subjects":["CIK:"+cik],"published_at":None,"first_available_at":None,
            "effective_at":None,"evidence_sha256":digest,"delivery":"poll",
            "expiry_seconds":86400},received_at=response.fetched_at))
        if len(events)>=1000:
            break
    return {"events":events,"filings":filings,"transport":response.evidence(),"coverage_complete":False,
            "acceptance_is_not_verified_publication":True,"external_ai_allowed":False}
