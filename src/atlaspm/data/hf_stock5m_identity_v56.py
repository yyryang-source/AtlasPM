"""Historical SEC cover-page observations (not certified security identities).

Select historical issuer filings independently of today's ticker directory.
Explicit same-row iXBRL DEI facts or strict untagged cover tables qualify.
"""
from __future__ import annotations

import argparse
from collections import Counter
from html.parser import HTMLParser
import json
import os
import re
import time

from atlaspm.data import sec_legacy_cover
from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data import hf_stock5m_filings_v52 as filings
from atlaspm.data import hf_stock5m_evidence_v55 as matching
from atlaspm.data import hf_stock5m_session_v4 as v4
from atlaspm.data.hf_stock5m_certification_v5 import ROOT
from atlaspm.data.market_db import private_path

BASE=ROOT/"identity_v56"
SCHEMA="atlaspm_historical_cover_observations_v56"
FIELDS={"dei:security12btitle":"title","dei:tradingsymbol":"symbol",
        "dei:securityexchangename":"exchange"}
EXCHANGES={"NASDAQ":"XNAS","NYSE":"XNYS","NYSEAMER":"XASE","NYSEARCA":"ARCX",
           "BATS":"BATS","IEX":"IEXG","THE NASDAQ STOCK MARKET LLC":"XNAS",
           "THE NASDAQ STOCK MARKET":"XNAS","NASDAQ CAPITAL MARKET":"XNAS",
           "NASDAQ GLOBAL MARKET":"XNAS","NASDAQ GLOBAL SELECT MARKET":"XNAS",
           "NEW YORK STOCK EXCHANGE":"XNYS","NEW YORK STOCK EXCHANGE LLC":"XNYS",
           "NYSE AMERICAN":"XASE","NYSE AMERICAN LLC":"XASE","NYSE ARCA":"ARCX",
           "NYSE ARCA, INC.":"ARCX"}
SYMBOL=re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,31}")
FILED=re.compile(r"(?im)^\s*FILED AS OF DATE:\s*(\d{8})\s*$")


class CoverRows(HTMLParser):
    """Do not combine facts across table rows or XBRL contexts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.row=None
        self.depth=0
        self.capture=None
        self.rows=[]
        self.bad=False

    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=="tr":
            self.depth+=1
            if self.depth==1:
                self.row=[]
                self.bad=False
            else:
                self.bad=True
        if tag=="ix:nonnumeric" and self.depth==1:
            name=a.get("name","").lower()
            if self.capture is not None:
                self.bad=True
            elif name in FIELDS:
                # Refuse continued/nil facts instead of guessing their text.
                if a.get("continuedat") or a.get("xsi:nil","").lower()=="true":
                    self.bad=True
                self.capture=(FIELDS[name],a.get("contextref",""),[])

    def handle_data(self,data):
        if self.capture is not None:
            self.capture[2].append(data)

    def handle_endtag(self,tag):
        if tag=="ix:nonnumeric" and self.capture is not None:
            field,context,parts=self.capture
            if self.row is not None:
                self.row.append((field,context," ".join("".join(parts).split())))
            self.capture=None
        if tag=="tr" and self.depth:
            self.depth-=1
            if self.depth==0:
                if not self.bad and self.capture is None and self.row:
                    self.rows.append(self.row)
                self.row=None
                self.capture=None


def extract(data,filing):
    """Point observations only; no effective interval or stable ID inferred."""
    if not matching._header(data,filing):
        raise ValueError("ISSUER_OR_ACCESSION_HEADER_UNRESOLVED")
    text=data.decode("utf-8",errors="replace")
    header=re.split(r"<DOCUMENT>",text[:65536],maxsplit=1,flags=re.I)[0]
    if FILED.findall(header)!=[filing["filed_date"].replace("-","")]:
        raise ValueError("FILING_DATE_HEADER_UNRESOLVED")
    primary=[]
    for doc in re.finditer(r"<DOCUMENT>(.*?)</DOCUMENT>",text,re.I|re.S):
        form=re.search(r"(?im)^\s*<TYPE>\s*([^\r\n<]+)",doc.group(1))
        if form and form.group(1).strip().upper()==filing["form"]:
            primary.append(doc.group(1))
    if len(primary)!=1:
        raise ValueError("PRIMARY_DOCUMENT_UNRESOLVED")
    parser=CoverRows()
    parser.feed(primary[0])
    parser.close()
    observations=[]
    rejected=0
    seen=set()
    for row in parser.rows:
        contexts={c for _,c,_ in row}
        if len(row)!=3 or len(contexts)!=1 or not next(iter(contexts)) or {f for f,_,_ in row}!=set(FIELDS.values()):
            rejected+=1
            continue
        values={f:v for f,_,v in row}
        symbol=values["symbol"]
        mic=EXCHANGES.get(values["exchange"].upper())
        if not SYMBOL.fullmatch(symbol) or not mic or not 1<=len(values["title"])<=500:
            rejected+=1
            continue
        key=(symbol,mic,values["title"],next(iter(contexts)))
        if key in seen:
            continue
        seen.add(key)
        observations.append({"symbol":symbol,"exchange_mic_candidate":mic,"security_title":values["title"],
            "exchange_reported":values["exchange"],"context_ref":next(iter(contexts)),"method":"SAME_ROW_DEI",
            "observed_filing_date":filing["filed_date"],"known_date_lower_bound":filing["filed_date"],
            "security_id":None,"valid_from":None,"valid_to":None,
            "status":"SEC_COVER_OBSERVATION_REVIEW_REQUIRED"})
    # Never fall back to plain text in a tagged document. In particular,
    # inconsistent contexts must not be accepted by dropping their tags.
    if not re.search(r"<\s*ix:",primary[0],re.I):
        for title,symbol,exchange in sec_legacy_cover.rows(primary[0]):
            mic=EXCHANGES.get(exchange.upper())
            if not SYMBOL.fullmatch(symbol) or not mic or not 1<=len(title)<=500:
                rejected+=1
                continue
            key=(symbol,mic,title,None)
            if key in seen:
                continue
            seen.add(key)
            observations.append({"symbol":symbol,"exchange_mic_candidate":mic,"security_title":title,
                "exchange_reported":exchange,"context_ref":None,"method":"EXPLICIT_LEGACY_TABLE",
                "observed_filing_date":filing["filed_date"],"known_date_lower_bound":filing["filed_date"],
                "security_id":None,"valid_from":None,"valid_to":None,
                "status":"SEC_COVER_OBSERVATION_REVIEW_REQUIRED"})
    conflicts={s for s,n in Counter(o["symbol"] for o in observations).items() if n>1}
    return {"observations":[o for o in observations if o["symbol"] not in conflicts],
            "conflicting_symbols":sorted(conflicts),"rejected_tagged_rows":rejected,
            "status":"COVER_OBSERVATIONS_REVIEW_REQUIRED" if observations and not conflicts
                     else "AMBIGUOUS_COVER_OBSERVATIONS" if conflicts else "NO_SUPPORTED_COVER_FACTS"}


def _cik(value):
    if not isinstance(value,str) or not re.fullmatch(r"[0-9]{1,10}",value):
        raise ValueError("Explicit numeric CIK required")
    return value.zfill(10)


def plan(year,*,ciks=None,after_cik=None,max_issuers=20):
    if type(year) is not int or not 2000<=year<=2026 or type(max_issuers) is not int or not 1<=max_issuers<=100:
        raise ValueError("Year 2000..2026 and issuer limit 1..100 required")
    if ciks is not None and (not isinstance(ciks,list) or not 1<=len(ciks)<=100):
        raise ValueError("CIK list must contain 1..100 entries")
    ids=sorted({_cik(c) for c in ciks}) if ciks is not None else None
    cursor=_cik(after_cik) if after_cik is not None else ""
    import duckdb
    db=private_path(filings.BASE/"index.duckdb",exists=True)
    cx=duckdb.connect(str(db),read_only=True)
    try:
        partitions=cx.execute("SELECT quarter,digest,parser_version,candidate_rows FROM partitions WHERE starts_with(quarter,?) ORDER BY quarter",[str(year)]).fetchall()
        expected=list(ev.quarters(f"{year}-Q1",f"{year}-Q{1 if year==2026 else 4}"))
        where="filed_date BETWEEN ? AND ? AND form IN ('8-K','6-K') AND cik>?"
        params=[f"{year}-01-01",f"{year}-03-31" if year==2026 else f"{year}-12-31",cursor]
        if ids is not None:
            where+=" AND cik IN (SELECT unnest(?))"
            params.append(ids)
        issuers=[r[0] for r in cx.execute(f"SELECT DISTINCT cik FROM filings WHERE {where} ORDER BY cik LIMIT ?",[*params,max_issuers+1]).fetchall()]
        selected=issuers[:max_issuers]
        rows=cx.execute(f"""SELECT quarter,cik,issuer,form,filed_date,source_url FROM filings
             WHERE {where} AND cik IN (SELECT unnest(?))
             QUALIFY row_number() OVER (PARTITION BY cik,quarter ORDER BY filed_date DESC,source_url)=1
             ORDER BY cik,quarter""",[*params,selected]).fetchall() if selected else []
    finally:
        cx.close()
    return {"year":year,"after_cik":cursor or None,"requested_ciks":ids,
            "selected_issuers":selected,"more_issuers":len(issuers)>max_issuers,
            "next_after_cik":selected[-1] if len(issuers)>max_issuers else None,
            "missing_index_quarters":sorted(set(expected)-{p[0] for p in partitions}),
            "index_partition_sha256":ev._sha(json.dumps(partitions,separators=(",",":")).encode()),
            "filings":[{"quarter":q,"cik":c,"issuer":i,"form":f,"filed_date":d.isoformat(),"source_url":u}
                       for q,c,i,f,d,u in rows]}


def run(year,*,ciks=None,after_cik=None,max_issuers=20,fetch=False,max_requests=20):
    if type(max_requests) is not int or not 1<=max_requests<=100:
        raise ValueError("Request budget 1..100 required")
    report={"schema":SCHEMA,**v4.GUARDS,**plan(year,ciks=ciks,after_cik=after_cik,max_issuers=max_issuers),
            "local_only":not fetch,"requests_attempted":0,"network_stopped":False,
            "status":"EVIDENCE_REVIEW_REQUIRED"}
    agent=os.environ.get("ATLASPM_SEC_USER_AGENT","")
    if fetch and (not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+",agent) or "\n" in agent or "\r" in agent):
        raise ValueError("Set ATLASPM_SEC_USER_AGENT to name and real contact email")
    results=[]
    for filing in report["filings"]:
        url=filing["source_url"]
        try:
            if not filings.URL.fullmatch(url):
                raise ValueError("UNSAFE_SOURCE_URL")
            manifest=private_path(filings.BASE/"body_manifests"/(ev._sha(url.encode())+".json"))
            if manifest.is_file():
                m=json.loads(manifest.read_text(encoding="utf-8"))
                digest=m["sha256"]
                data,path=matching._body({**filing,"sha256":digest})
            elif fetch and not report["network_stopped"] and report["requests_attempted"]<max_requests:
                report["requests_attempted"]+=1
                try:
                    data=ev.download(url,agent)
                except ValueError:
                    report["network_stopped"]=True
                    raise
                finally:
                    time.sleep(.5)
                report["network_downloads"]+=1
                digest=ev._sha(data)
                path=private_path(filings.BASE/"bodies"/(digest+".bin"))
                ev._write(path,data)
                ev._json(manifest,{"source_url":url,"sha256":digest,"retrieved_utc":ev.now()})
            else:
                results.append({**filing,"status":"NOT_CACHED"})
                continue
            try:
                result=extract(data,filing)
            except ValueError as exc:
                result={"status":"BODY_REVIEW_REQUIRED","reason":str(exc),"observations":[]}
            results.append({**filing,"sha256":digest,"evidence_path":str(path),**result})
        except (ValueError,OSError,KeyError) as exc:
            results.append({**filing,"status":"COLLECTION_OR_CACHE_ERROR","reason":str(exc)})
    counts=Counter(x["status"] for x in results)
    report.update(results=results,status_counts=dict(counts),
                  cover_observations=sum(len(x.get("observations",[])) for x in results),
                  filings_selected=len(results),filings_with_bodies=sum("sha256" in x for x in results),
                  certified_identities=0,claims_ingested=0,
                  page_complete=not any(counts[k] for k in ("NOT_CACHED","COLLECTION_OR_CACHE_ERROR")),
                  warning="Sampled SEC cover observations are issuer-level leads, not independent sources, PIT intervals, or verified historical share classes.")
    # Cursor only advances automatically after all selected bodies were processed.
    report["resume_after_cik"]=report["next_after_cik"] if report["page_complete"] else report["after_cik"]
    report["page_advance_allowed"]=report["page_complete"] and not report["missing_index_quarters"]
    key=ev._sha(json.dumps([year,report["after_cik"],report["selected_issuers"],report["index_partition_sha256"]]).encode())[:24]
    path=BASE/"pages"/(key+".json")
    report["report_path"]=str(private_path(path))
    ev._json(path,report)
    ev._json(BASE/"latest.json",report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year",type=int,required=True)
    p.add_argument("--ciks",nargs="+")
    p.add_argument("--after-cik")
    p.add_argument("--max-issuers",type=int,default=20)
    p.add_argument("--max-requests",type=int,default=20)
    p.add_argument("--fetch",action="store_true")
    a=p.parse_args()
    print(json.dumps(run(a.year,ciks=a.ciks,after_cik=a.after_cik,max_issuers=a.max_issuers,
                         fetch=a.fetch,max_requests=a.max_requests),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
