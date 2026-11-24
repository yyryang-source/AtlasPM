"""V5.4: targeted historical evidence discovery from local OHLCV anomalies.

All mappings from present-day issuer lists are search leads, never historical
security identities. Price gaps are observations, never certified actions.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import json
import math
import re

from atlaspm.data import factor_db as factors
from atlaspm.data import hf_stock5m as hf
from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data import hf_stock5m_filings_v52 as filings
from atlaspm.data import hf_stock5m_session_v4 as v4
from atlaspm.data.hf_stock5m_certification_v5 import ROOT
from atlaspm.data.market_db import private_path

BASE = ROOT / "discovery_v54"
VERSION = "v54.1"
RATIOS = (2,3,4,5,6,7,8,10,12,15,20,25,30,40,50,75,100,200,500,1000)


def prior_month(month):
    d=date.fromisoformat(hf.validate_month(month)+"-01")
    return (d-timedelta(days=1)).strftime("%Y-%m")


def _daily(cx,month):
    path=private_path(hf.output_path(month,"rth"),exists=True)
    signature=v4._signature(path)
    calendar=v4.calendar_rows(month)
    cal_hash=ev._sha(json.dumps(calendar,default=str).encode())
    cache=private_path(BASE/"daily"/(month+".parquet"))
    meta=cache.with_suffix(".json")
    identity={"version":VERSION,"source_signature":signature,"calendar_sha256":cal_hash}
    if cache.is_file() and meta.is_file():
        m=json.loads(meta.read_text(encoding="utf-8"))
        if all(m.get(k)==v for k,v in identity.items()) and m.get("sha256")==_hash(cache):
            return cache,calendar,True
    cache.parent.mkdir(parents=True,exist_ok=True)
    stage=cache.with_suffix(".parquet.part")
    try:
        view=v4.install_research_view(cx,month)
        # Aggregate only internally valid full-slot bars. A duplicate timestamp
        # invalidates that symbol-day rather than arbitrarily choosing an open.
        cx.execute(f"""COPY (
            SELECT symbol,trade_date_et AS trade_date,
              arg_min(open,timestamp_ms)::DOUBLE AS day_open,
              arg_max(close,timestamp_ms)::DOUBLE AS day_close,
              sum(volume_shares)::DOUBLE AS volume,
              count(*) AS bars, count(DISTINCT timestamp_ms) AS unique_bars,
              min(timestamp_ms) AS first_ms,max(timestamp_ms) AS last_ms
            FROM {view}
            WHERE symbol IS NOT NULL AND length(symbol) BETWEEN 1 AND 32
            GROUP BY 1,2 HAVING count(*)=count(DISTINCT timestamp_ms)
              AND bool_and(coalesce(isfinite(open) AND open>0 AND isfinite(close) AND close>0
              AND isfinite(high) AND isfinite(low) AND low>0
              AND high>=greatest(open,close,low) AND low<=least(open,close)
              AND isfinite(volume_shares) AND volume_shares>=0,false))
            ORDER BY symbol,trade_date
            ) TO {factors._quoted(stage)} (FORMAT PARQUET,COMPRESSION ZSTD)""")
        if signature!=v4._signature(path):
            raise ValueError("Source changed during scan")
        digest=_hash(stage)
        stage.replace(cache)
        ev._json(meta,{**identity,"sha256":digest})
    finally:
        stage.unlink(missing_ok=True)
    return cache,calendar,False


def _hash(path):
    from atlaspm.data.hf_stock5m_certification_v5 import fingerprint
    return fingerprint(path)["sha256"]


def _candidates(cx,paths,calendar,month,threshold,limit):
    cx.execute("CREATE OR REPLACE TEMP TABLE discovery_sessions(trade_date DATE, ordinal INTEGER, open_ms BIGINT, close_ms BIGINT)")
    cx.executemany("INSERT INTO discovery_sessions VALUES (?,?,?,?)",
                   [(r[0],i,r[1],r[2]) for i,r in enumerate(sorted(calendar),1)])
    src=factors._files_sql(paths)
    # Require prior final and current first full calendar slot, not merely
    # adjacent observations. Interior completeness is not established.
    sql=f"""WITH daily AS (
      SELECT d.*,s.ordinal,s.open_ms,s.close_ms
      FROM {src} d JOIN discovery_sessions s USING(trade_date)
    ), lagged AS (
      SELECT *,lag(trade_date) OVER w AS prior_date,lag(day_close) OVER w AS prior_close,
      lag(ordinal) OVER w AS prior_ordinal,lag(last_ms) OVER w AS prior_last,
      lag(close_ms) OVER w AS prior_session_close
      FROM daily WINDOW w AS (PARTITION BY symbol ORDER BY trade_date)
    ), moves AS (
      SELECT *,day_open/prior_close AS price_ratio,ordinal-prior_ordinal AS session_gap
      FROM lagged WHERE prior_close>0 AND day_open>0 AND strftime(trade_date,'%Y-%m')=?
    ), selected AS (
      SELECT *,CASE WHEN session_gap=1 THEN 'OVERNIGHT_PRICE_GAP' ELSE 'OBSERVATION_GAP' END AS kind,
        CASE WHEN session_gap=1 THEN abs(ln(price_ratio)) ELSE ln(session_gap::DOUBLE) END AS score
      FROM moves WHERE isfinite(price_ratio) AND price_ratio>0 AND ((session_gap=1
        AND first_ms=((open_ms+299999)//300000)*300000
        AND prior_last=(prior_session_close//300000)*300000-300000
        AND (price_ratio>=? OR price_ratio<=1.0/?))
        OR session_gap>=10)
    )
    SELECT symbol,trade_date,prior_date,prior_close,day_open,price_ratio,session_gap,kind,score,
      count(*) OVER() AS total
    FROM selected ORDER BY score DESC,symbol,trade_date LIMIT ?"""
    rows=cx.execute(sql,[month,threshold,threshold,limit]).fetchall()
    output=[]
    for symbol,day,prev,old,new,ratio,gap,kind,score,total in rows:
        candidate={"symbol":symbol,"date":day.isoformat(),"previous_date":prev.isoformat(),
                   "previous_close":old,"observed_open":new,"price_ratio":ratio,
                   "session_gap":gap,"kind":kind,"priority_score":score,
                   "status":"UNVERIFIED_MARKET_OBSERVATION"}
        candidate["event_id"]=ev._sha(json.dumps([symbol,day.isoformat(),kind]).encode())
        if kind=="OVERNIGHT_PRICE_GAP":
            # Price change is the reciprocal of new/old shares, if a split.
            nearest=min([float(r) for r in RATIOS]+[1/r for r in RATIOS],
                        key=lambda x:abs(math.log(ratio)-math.log(x)))
            candidate["split_like_price_ratio"]=nearest if abs(math.log(ratio)-math.log(nearest))<=math.log(1.10) else None
        output.append(candidate)
    return output, int(rows[0][-1]) if rows else 0


def scan(month,*,threshold=1.5,limit=200,memory="2GB",threads=2):
    month=hf.validate_month(month)
    if not math.isfinite(threshold) or not 1.1<=threshold<=100 or not 1<=limit<=10000:
        raise ValueError("Invalid threshold or candidate limit")
    cx=factors._db(memory,threads)
    paths,calendar,parts=[],[],[]
    try:
        for m in (prior_month(month),month):
            if m<"2000-01":
                continue
            if m!=month and not private_path(hf.output_path(m,"rth")).is_file():
                parts.append({"month":m,"status":"MISSING_LOOKBACK"})
                continue
            p,cal,cached=_daily(cx,m)
            paths.append(p)
            calendar.extend(cal)
            parts.append({"month":m,"status":"REUSED" if cached else "DERIVED","sha256":_hash(p)})
        candidates,total=_candidates(cx,paths,calendar,month,threshold,limit)
    finally:
        cx.close()
    report={"schema":"atlaspm_market_event_discovery_v54",**v4.GUARDS,
            "month":month,"partitions":parts,"threshold":threshold,
            "candidate_count":len(candidates),"total_detected":total,
            "truncated":total>len(candidates),"candidates":candidates,
            "status":"RESEARCH_CANDIDATES_ONLY",
            "warning":"First/last observations and gaps do not prove IPO/delisting or corporate actions. Lookback is one month."}
    path=BASE/"queues"/(month+".json")
    report["summary_path"]=str(private_path(path))
    ev._json(path,report)
    return report


def issuer_leads():
    """Latest locally captured SEC ticker snapshot, strictly search-only."""
    manifests=private_path(ev.BASE/"manifests")
    for path in sorted(manifests.glob("sec-current-*.json"),reverse=True):
        m=json.loads(path.read_text(encoding="utf-8"))
        digest=m.get("sha256","")
        if m.get("source_url")!=ev.SEC_TICKERS or m.get("scope")!="CURRENT_ISSUER_MAPPING" or not re.fullmatch("[0-9a-f]{64}",digest):
            continue
        data=private_path(ev.BASE/"objects"/(digest+".bin"),exists=True).read_bytes()
        if ev._sha(data)!=digest:
            raise ValueError("SEC issuer snapshot hash mismatch")
        _,rows=ev.parse_sec_tickers(data)
        out=defaultdict(set)
        for row in rows:
            out[str(row["ticker"]).upper()].add(str(row["cik"]).zfill(10))
        return out,{"retrieved_utc":m["retrieved_utc"],"sha256":digest}
    return {},None


def _kind(candidate):
    return "split" if candidate["kind"]=="OVERNIGHT_PRICE_GAP" else "exit"


def investigate(month,*,max_events=10,per_event=5,fetch=False,max_requests=20):
    month=hf.validate_month(month)
    if not 1<=max_events<=100 or not 1<=per_event<=20 or not 1<=max_requests<=100:
        raise ValueError("Invalid event/filing/request budget")
    path=private_path(BASE/"queues"/(month+".json"),exists=True)
    queue=json.loads(path.read_text(encoding="utf-8"))
    if queue.get("schema")!="atlaspm_market_event_discovery_v54" or queue.get("month")!=month:
        raise ValueError("Invalid queue")
    mapping,source=issuer_leads()
    candidates=queue["candidates"]
    eligible=sum(len(mapping.get(x["symbol"],set()))==1 for x in candidates)
    # Prioritize investigable leads without hiding unresolved queue coverage.
    ordered=sorted(candidates,key=lambda x:len(mapping.get(x["symbol"],set()))!=1)
    results=[]
    requests=0
    downloads=0
    network_stopped=False
    for item in ordered[:max_events]:
        symbol=item["symbol"]
        ciks=sorted(mapping.get(symbol,set()))
        row={"event_id":item["event_id"],"symbol":symbol,"date":item["date"],
             "mapping_status":"CURRENT_SNAPSHOT_SEARCH_LEAD_ONLY","candidate_ciks":ciks}
        if len(ciks)!=1:
            row["status"]="NO_UNAMBIGUOUS_ISSUER_SEARCH_LEAD"
        else:
            day=date.fromisoformat(item["date"])
            start=(day-timedelta(days=14)).isoformat()
            end=(day+timedelta(days=30)).isoformat()
            args=(ciks,start,end,_kind(item),per_event)
            if fetch and requests<max_requests and not network_stopped:
                evidence=filings.collect(*args,fetch=True,max_requests=max_requests-requests)
                requests+=evidence["requests_attempted"]
                downloads+=evidence["network_downloads"]
                # Any failed attempted download prevents repeated provider calls.
                if evidence["requests_attempted"]>evidence["network_downloads"]:
                    network_stopped=True
            else:
                evidence=filings.collect(*args)
            row.update({"status":"REVIEW_REQUIRED","filings_selected":len(evidence["filings"]),
                        "filings_parsed":evidence["filings_parsed"],
                        "hints_count":evidence["hints_count"],
                        "missing_requested_quarters":evidence["missing_requested_quarters"],
                        "truncated":evidence["truncated"],"filings":evidence["results"]})
        results.append(row)
    report={"schema":"atlaspm_targeted_filing_discovery_v54",**v4.GUARDS,
            "local_only":not fetch,"network_downloads":downloads,"requests_attempted":requests,
            "network_stopped":network_stopped,"month":month,"issuer_snapshot":source,
            "events_considered":len(results),"queue_candidates":len(candidates),
            "unambiguous_current_leads":eligible,"results":results,
            "status":"EVIDENCE_REVIEW_REQUIRED",
            "warning":"Issuer leads are contemporary, not historical. Filing hints are unreviewed. No V5.3 claims created."}
    report["summary_path"]=str(private_path(BASE/"latest.json"))
    ev._json(BASE/"latest.json",report)
    return report
