"""V5.5: resumable retrospective event matching; never self-certify evidence.

Raw SEC body provenance, issuer header, explicit split-date wording and
market ratios are diagnostic signals. They are not independent sources or
proof of historical share-class identity. Reviewed claims use V5.3 ingest.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit
import json
import math
import re

from atlaspm.data import hf_stock5m as hf
from atlaspm.data import hf_stock5m_discovery_v54 as discovery
from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data import hf_stock5m_filings_v52 as filings
from atlaspm.data import hf_stock5m_session_v4 as v4
from atlaspm.data.hf_stock5m_certification_v5 import ROOT
from atlaspm.data.market_db import private_path

BASE = ROOT / "matching_v55"
MONTHS = ("January","February","March","April","May","June","July","August",
          "September","October","November","December")
DATE_PATTERN = r"(?:\d{4}-\d{2}-\d{2}|(?:"+"|".join(MONTHS)+r")\s+\d{1,2},?\s+\d{4})"
EFFECTIVE = re.compile(r"\beffective\s+(?:as of\s+|on\s+)?("+DATE_PATTERN+r")\b",re.I)
UNCERTAIN = re.compile(r"\b(?:proposed|proposal|subject to|may|might|could|if approved|not effective|cancelled|canceled|expected|up to)\b",re.I)
HEADER_CIK = re.compile(r"(?im)^\s*CENTRAL INDEX KEY:\s*([0-9]{1,10})\s*$")
HEADER_ACCESSION = re.compile(r"(?im)^\s*ACCESSION NUMBER:\s*([0-9-]+)\s*$")


def _date(text):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}",text):
        return date.fromisoformat(text).isoformat()
    month,day,year=text.replace(",","").split()
    return date(int(year),[m.lower() for m in MONTHS].index(month.lower())+1,int(day)).isoformat()


def _body(filing):
    url=filing["source_url"]
    digest=filing["sha256"]
    if not filings.URL.fullmatch(url) or not re.fullmatch("[0-9a-f]{64}",digest):
        raise ValueError("Invalid filing provenance")
    key=ev._sha(url.encode())
    manifest=private_path(filings.BASE/"body_manifests"/(key+".json"),exists=True)
    m=json.loads(manifest.read_text(encoding="utf-8"))
    if m.get("source_url")!=url or m.get("sha256")!=digest:
        raise ValueError("Body manifest mismatch")
    path=private_path(filings.BASE/"bodies"/(digest+".bin"),exists=True)
    data=path.read_bytes()
    if ev._sha(data)!=digest:
        raise ValueError("Body hash mismatch")
    return data,path


def _header(data,filing):
    text=data[:65536].decode("latin-1")
    # Header only: do not trust CIKs occurring inside an exhibit or narrative.
    text=re.split(r"<DOCUMENT>",text,maxsplit=1,flags=re.I)[0]
    ciks={x.zfill(10) for x in HEADER_CIK.findall(text)}
    accession=HEADER_ACCESSION.findall(text)
    expected=filing["source_url"].rsplit("/",1)[-1][:-4]
    return ciks=={str(filing["cik"]).zfill(10)} and accession==[expected]


def match_split(event,filing,data):
    """Compare explicitly dated split text with a market observation.

    Date/ratio are hypotheses, not a verified effective date or known date.
    Conservative regex intentionally misses complex and exhibit-only wording.
    """
    result={"source_url":filing["source_url"],"sha256":filing["sha256"],
            "cik":filing["cik"],"filed_date":filing["filed_date"],
            "issuer_header_matches":_header(data,filing),"candidates":[]}
    if not result["issuer_header_matches"]:
        result["status"]="ISSUER_OR_ACCESSION_HEADER_UNRESOLVED"
        return result
    extracted=filings.extract(data,filing)
    ticker=re.escape(event["symbol"])
    # A terminal sentence period is punctuation, while .B or -B is a
    # share-class suffix and must not be mistaken for the requested ticker.
    ticker_re=re.compile(r"\b(?:trading|ticker)\s+symbol\s+(?:of\s+|is\s+|[:\s]*)[\"'“]?"+ticker+r"(?![A-Za-z0-9]|[.\-][A-Za-z0-9])",re.I)
    terms=set()
    observed=float(event["price_ratio"])
    if not math.isfinite(observed) or observed<=0:
        raise ValueError("Invalid observed ratio")
    for hint in extracted["hints"]:
        if hint["kind"]!="SPLIT_RATIO":
            continue
        a,b=hint["ratio_terms"]
        if min(a,b)<=0 or a==b:
            continue
        ratio=Fraction(a,b)
        excerpt=hint["excerpt"]
        dates=set()
        for m in EFFECTIVE.finditer(excerpt):
            try:
                dates.add(_date(m.group(1)))
            except ValueError:
                continue
        terms.add(str(ratio))
        normalized=observed*float(ratio)
        result["candidates"].append({
            "new_over_old_shares":str(ratio),"effective_date_mentions":sorted(dates),
            "observed_date_matches":dates=={event["date"]},
            "market_ratio_matches":abs(normalized-1)<=0.15,
            "normalized_overnight_return_if_split":normalized-1,
            "explicit_ticker_mention":bool(ticker_re.search(excerpt)),
            "uncertain_language":bool(UNCERTAIN.search(excerpt)),
            "excerpt":excerpt,"offset":hint["offset"]})
    result["truncated"]=extracted["truncated"]
    result["ratio_terms"]=sorted(terms)
    good=[x for x in result["candidates"] if x["observed_date_matches"] and
          x["market_ratio_matches"] and x["explicit_ticker_mention"] and not x["uncertain_language"]]
    if len(terms)>1 or extracted["multiple_ratio_candidates"]:
        result["status"]="AMBIGUOUS_RATIO_TERMS"
    elif good and not extracted["truncated"]:
        result["status"]="MARKET_AND_TEXT_AGREE_REVIEW_REQUIRED"
    elif result["candidates"]:
        result["status"]="SPLIT_TERMS_REQUIRE_REVIEW"
    else:
        result["status"]="NO_SPLIT_RATIO_HINT"
    return result


def load_reference(filename):
    """Optional reviewed external split assertions; provenance, not certification."""
    result={}
    if filename is None:
        return result
    source=private_path(Path(filename),exists=True)
    checked={}
    fields={"symbol","effective_date","new_over_old_shares","security_id",
            "source_family","source_url","evidence_path","evidence_sha256","known_date"}
    for number,line in enumerate(source.read_text(encoding="utf-8-sig").splitlines(),1):
        if not line.strip():
            continue
        try:
            r=json.loads(line)
            if set(r)!=fields or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,31}",r["symbol"]):
                raise ValueError("Invalid reference fields or ticker")
            for field in ("effective_date","known_date"):
                if date.fromisoformat(r[field]).isoformat()!=r[field]:
                    raise ValueError("Noncanonical reference date")
            for field in ("security_id","source_family"):
                if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}",r[field]):
                    raise ValueError("Invalid reference identifier")
            if not re.fullmatch(r"[0-9]{1,7}/[0-9]{1,7}",r["new_over_old_shares"]):
                raise ValueError("Use positive integer numerator/denominator")
            ratio=Fraction(r["new_over_old_shares"])
            if ratio<=0 or ratio==1:
                raise ValueError("Invalid reference split ratio")
            url=urlsplit(r["source_url"])
            if url.scheme!="https" or not url.hostname or url.hostname=="sec.gov" or url.hostname.endswith(".sec.gov"):
                raise ValueError("External HTTPS evidence source required")
            p=private_path(Path(r["evidence_path"]),exists=True)
            if p.is_relative_to(private_path(ROOT)):
                raise ValueError("Use original external evidence, not pipeline artifacts")
            if p not in checked:
                from atlaspm.data.hf_stock5m_certification_v5 import fingerprint
                checked[p]=fingerprint(p)["sha256"]
            if checked[p]!=r["evidence_sha256"]:
                raise ValueError("External evidence hash mismatch")
            r["new_over_old_shares"]=str(ratio)
            result.setdefault((r["symbol"],r["effective_date"]),[]).append(r)
        except Exception as exc:
            raise ValueError(f"External reference line {number}: {exc}") from exc
    return result


def review(queue,investigation,reference=None):
    indexed={e["event_id"]:e for e in queue["candidates"]}
    outcomes=[]
    seen=set()
    for row in investigation["results"]:
        if row["event_id"] in seen:
            raise ValueError("Duplicate investigation event")
        seen.add(row["event_id"])
        event=indexed.get(row["event_id"])
        if not event or event["symbol"]!=row["symbol"] or event["date"]!=row["date"]:
            raise ValueError("Investigation does not match market queue")
        out={"event_id":event["event_id"],"symbol":event["symbol"],"date":event["date"],
             "kind":event["kind"],"filings":[],"candidate_ciks":row["candidate_ciks"]}
        if event["kind"]!="OVERNIGHT_PRICE_GAP":
            out["status"]="OBSERVATION_GAP_REVIEW_REQUIRED"
        else:
            for f in row.get("filings",[]):
                if "sha256" not in f:
                    continue
                try:
                    data,path=_body(f)
                    item=match_split(event,f,data)
                    item["evidence_path"]=str(path)
                    out["filings"].append(item)
                except (OSError,ValueError,KeyError,TypeError) as exc:
                    out["filings"].append({"source_url":f.get("source_url"),
                                           "status":"EVIDENCE_ERROR","detail":str(exc)})
            statuses={x["status"] for x in out["filings"]}
            terms={t for x in out["filings"] for t in x.get("ratio_terms",[])}
            if len(terms)>1 or "AMBIGUOUS_RATIO_TERMS" in statuses:
                out["status"]="CONFLICTING_OR_AMBIGUOUS_RATIO_TERMS"
            elif "EVIDENCE_ERROR" in statuses or "ISSUER_OR_ACCESSION_HEADER_UNRESOLVED" in statuses:
                out["status"]="EVIDENCE_INTEGRITY_OR_IDENTITY_REVIEW"
            elif "MARKET_AND_TEXT_AGREE_REVIEW_REQUIRED" in statuses:
                out["status"]="MARKET_AND_TEXT_AGREE_REVIEW_REQUIRED"
            elif not out["filings"]:
                out["status"]="NO_PARSED_FILING_EVIDENCE"
            else:
                out["status"]="FILING_TERMS_REVIEW_REQUIRED"
        refs=(reference or {}).get((event["symbol"],event["date"]),[])
        out["external_reference_claims"]=refs
        if refs and event["kind"]=="OVERNIGHT_PRICE_GAP":
            ext_terms={r["new_over_old_shares"] for r in refs}
            ids={r["security_id"] for r in refs}
            sec_terms={t for x in out["filings"] for t in x.get("ratio_terms",[])}
            sec_hashes={x.get("sha256") for x in out["filings"]}
            if len(ext_terms)>1 or len(ids)>1 or (sec_terms and ext_terms!=sec_terms):
                out["status"]="EXTERNAL_REFERENCE_CONFLICT"
            elif out["status"]=="MARKET_AND_TEXT_AGREE_REVIEW_REQUIRED" and all(
                    r["evidence_sha256"] not in sec_hashes for r in refs):
                out["status"]="EXTERNAL_REFERENCE_AGREES_REVIEW_REQUIRED"
        out["independent_source_verified"]=False
        out["historical_security_id"]=None
        out["approved_for_ingest"]=False
        outcomes.append(out)
    return outcomes


def run(start="2026-03",end="2026-03",*,threshold=1.5,limit=200,max_events=10,
        per_event=5,fetch=False,max_requests=20,memory="2GB",threads=2,reference_jsonl=None):
    chosen=hf.months(start,end,0)
    if not chosen or not 1<=max_requests<=100 or not 1<=max_events<=100 or not 1<=per_event<=20:
        raise ValueError("Invalid archive range or investigation budget")
    reference=load_reference(reference_jsonl)
    report={"schema":"atlaspm_event_matching_v55",**v4.GUARDS,
            "local_only":not fetch,"months_selected":len(chosen),"months":[],
            "requests_attempted":0,"network_stopped":False,
            "status":"EVIDENCE_REVIEW_REQUIRED","external_reference_rows":sum(map(len,reference.values()))}
    # No queue is assumed complete: each run rechecks local data/cache. Persist
    # every completed month's evidence before moving on; re-running reuses
    # daily Parquet and SEC bodies, including after an interrupted run.
    for month in chosen:
        try:
            q=discovery.scan(month,threshold=threshold,limit=limit,memory=memory,threads=threads)
            remaining=max_requests-report["requests_attempted"]
            online=fetch and remaining>0 and not report["network_stopped"]
            inv=discovery.investigate(month,max_events=max_events,per_event=per_event,
                                      fetch=online,max_requests=max(1,remaining))
            report["requests_attempted"]+=inv["requests_attempted"]
            report["network_downloads"]+=inv["network_downloads"]
            report["network_stopped"]|=inv["network_stopped"]
            outcomes=review(q,inv,reference)
            counts=dict(Counter(x["status"] for x in outcomes))
            detail={"month":month,"status":"REVIEW_REQUIRED","total_detected":q["total_detected"],
                    "queued":q["candidate_count"],"queue_truncated":q["truncated"],
                    "events_considered":len(outcomes),
                    "filings_parsed":sum(x.get("filings_parsed",0) for x in inv["results"]),
                    "unambiguous_current_leads":inv["unambiguous_current_leads"],
                    "status_counts":counts,"investigation":inv,"outcomes":outcomes}
            target=BASE/"months"/(month+".json")
            ev._json(target,detail)
            report["months"].append({k:v for k,v in detail.items() if k not in {"investigation","outcomes"}}
                                    | {"report_path":str(private_path(target))})
        except Exception as exc:
            # A failed month may already have attempted network requests.
            # Prevent further downloads in this run if its budget is unknown.
            report["network_stopped"]=True
            report["months"].append({"month":month,"status":"ERROR","detail":str(exc)})
        report["summary_path"]=str(private_path(BASE/"latest.json"))
        ev._json(BASE/"latest.json",report)
    report["months_completed"]=sum(x["status"]!="ERROR" for x in report["months"])
    report["events_detected"]=sum(x.get("total_detected",0) for x in report["months"])
    report["events_considered"]=sum(x.get("events_considered",0) for x in report["months"])
    report["filing_parse_observations"]=sum(x.get("filings_parsed",0) for x in report["months"])
    report["events_queued"]=sum(x.get("queued",0) for x in report["months"])
    report["detected_events_not_considered"]=report["events_detected"]-report["events_considered"]
    report["complete_historical_event_coverage_verified"]=False
    report["status_counts"]=dict(sum((Counter(x.get("status_counts",{})) for x in report["months"]),Counter()))
    report["certified_events"]=0
    report["claims_ingested"]=0
    report["warning"]="Retrospective research only. Market agreement is not independent source corroboration. Review and V5.3 ingest required."
    ev._json(BASE/"latest.json",report)
    return report
