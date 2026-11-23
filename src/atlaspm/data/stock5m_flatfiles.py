"""All-market US stock 5m: Massive historical 1m flat files to Parquet.

Source 2003-09-10 onward. Research only. Raw prices are split-unadjusted.
S3 Flat Files keys differ from the user's Massive REST API key.
"""
from __future__ import annotations
from datetime import date,timedelta
import os,sys
from pathlib import Path
from urllib.parse import urlencode
from atlaspm.data.market_db import private_path,SYMBOL_RE
from atlaspm.data.market_fetch import get_json,key_from_env
FLOOR=date(2003,9,10)
PREFIX="us_stocks_sip/minute_aggs_v1"
ROOT=Path("local_data/stock_5m")
FIVE=300_000_000_000

def sessions(start,end,dry=False):
    try:a,b=date.fromisoformat(start),date.fromisoformat(end)
    except ValueError as e:raise ValueError("Dates must be YYYY-MM-DD") from e
    if a>b:raise ValueError("start later than end")
    a=max(a,FLOOR)
    if a>b:return [],"BEFORE_PROVIDER_HISTORY"
    if dry:
        result=[];d=a
        while d<=b:
            if d.weekday()<5:result.append(d)
            d+=timedelta(days=1)
        return result,"WEEKDAYS_ESTIMATE"
    try:import pandas_market_calendars as mcal
    except ImportError as e:
        raise ValueError("Install NYSE calendar: pip install -e '.[lake,calendar]'") from e
    frame=mcal.get_calendar("NYSE").schedule(start_date=str(a),end_date=str(b))
    return [x.date() for x in frame.index],"NYSE"

def key(d):return f"{PREFIX}/{d:%Y/%m}/{d:%Y-%m-%d}.csv.gz"

def paths(d,session="rth"):
    root=private_path(ROOT)
    return (root/"raw"/f"{d:%Y}"/f"{d:%m}"/f"{d:%Y-%m-%d}.csv.gz",
            root/"parquet"/f"session={session}"/f"year={d:%Y}"/f"month={d:%m}"/f"{d:%Y-%m-%d}.parquet")

def s3(profile):
    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import ProfileNotFound
    except ImportError as e:
        raise ValueError("Install dependencies: pip install -e '.[lake,calendar]'") from e
    try:
        return boto3.Session(profile_name=profile).client(
            "s3",endpoint_url="https://files.massive.com",region_name="us-east-1",
            config=Config(signature_version="s3v4",retries={"max_attempts":3}))
    except ProfileNotFound as e:
        raise ValueError("Configure Massive S3 keys: aws configure --profile massive") from e

def fetch(s3client,d,raw):
    from botocore.exceptions import ClientError,NoCredentialsError,PartialCredentialsError
    private_path(raw);raw.parent.mkdir(parents=True,exist_ok=True)
    temp=raw.with_suffix(".csv.gz.part")
    try:
        s3client.download_file("flatfiles",key(d),str(temp))
        if temp.stat().st_size<100:raise ValueError("Empty minute flatfile")
        os.replace(temp,raw)
        return True
    except ClientError as e:
        code=str(e.response.get("Error",{}).get("Code","unknown"))
        if code in ("404","NoSuchKey","NotFound"):return False
        if code in ("403","401","AccessDenied","InvalidAccessKeyId","SignatureDoesNotMatch"):
            raise ValueError("No Stocks Flat Files access: check plan and separate S3 keys") from e
        raise ValueError(f"Massive S3 {code} for {d}") from e
    except (NoCredentialsError,PartialCredentialsError) as e:
        raise ValueError("Massive S3 credentials missing (REST API key is different)") from e
    finally:temp.unlink(missing_ok=True)

def quote(p):return str(private_path(p)).replace("\\","/").replace("'","''")

def sql(raw,out,d,session):
    if session not in ("rth","all"):raise ValueError("Invalid session")
    where=""
    if session=="rth":
        local="CAST(timezone('America/New_York',to_timestamp(window_start/1000000000.0)) AS TIME)"
        where=f"AND {local} >= TIME '09:30:00' AND {local} < TIME '16:00:00'"
    return f"""
COPY (
 WITH one AS (
  SELECT ticker,window_start,window_start-window_start%{FIVE} AS bucket,
         open,high,low,close,volume,transactions
  FROM read_csv('{quote(raw)}',header=true,auto_detect=false,
    columns={{'ticker':'VARCHAR','volume':'DOUBLE','open':'DOUBLE',
              'close':'DOUBLE','high':'DOUBLE','low':'DOUBLE',
              'window_start':'BIGINT','transactions':'BIGINT'}})
  WHERE ticker IS NOT NULL AND window_start>0
    AND open>0 AND high>0 AND low>0 AND close>0 AND volume>=0 {where}
 )
 SELECT ticker AS symbol,DATE '{d}' AS trade_date_et,
   CAST(bucket/1000000 AS BIGINT) AS timestamp_ms,
   arg_min(open,window_start) AS open,max(high) AS high,
   min(low) AS low,arg_max(close,window_start) AS close,
   sum(volume) AS volume_shares,sum(transactions) AS transactions,
   count(*) AS source_minute_bars
 FROM one GROUP BY ticker,bucket ORDER BY symbol,timestamp_ms
) TO '{quote(out)}' (FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 100000)
"""

def convert(raw,out,d,session):
    try:import duckdb
    except ImportError as e:raise ValueError("Install DuckDB: pip install -e '.[lake,calendar]'") from e
    private_path(raw,exists=True);private_path(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    temp=out.with_suffix(".parquet.part")
    try:
        con=duckdb.connect(":memory:")
        try:
            con.execute(sql(raw,temp,d,session))
            n=con.execute(f"SELECT COUNT(*) FROM read_parquet('{quote(temp)}')").fetchone()[0]
        finally:con.close()
        if n<1 or temp.stat().st_size<100:raise ValueError(f"No valid 5m bars for {d}")
        os.replace(temp,out)
        return n
    finally:temp.unlink(missing_ok=True)

def backfill(start,end,max_days=0,session="rth",profile="massive",
             dry_run=False,remove_raw=False,refresh=False):
    if max_days<0 or session not in ("rth","all"):raise ValueError("Invalid max-days/session")
    days,calendar=sessions(start,end,dry_run)
    selected=days[:max_days] if max_days else days
    report={"start_requested":start,"end_inclusive":end,
            "source_floor":"2003-09-10","source_before_2003_unavailable":start<"2003-09-10",
            "session":session,"calendar":calendar,"all_sessions":len(days),
            "selected_sessions":len(selected),"adjustment":"unadjusted",
            "research_only":True}
    if dry_run:
        return {**report,"mode":"dry_run","first_object":key(selected[0]) if selected else None,
                "last_object":key(selected[-1]) if selected else None,
                "warning":"Weekday estimate, ignores NYSE holidays and entitlement"}
    client=None;new=old=missing=bars=0
    for i,d in enumerate(selected,1):
        raw,out=paths(d,session)
        if out.is_file() and out.stat().st_size>100 and not refresh:
            old+=1;continue
        if not raw.is_file() or refresh:
            if client is None:client=s3(profile)
            if not fetch(client,d,raw):
                missing+=1
                print(f"[stock5m] {i}/{len(selected)} unavailable {d}",file=sys.stderr,flush=True)
                continue
        n=convert(raw,out,d,session)
        if remove_raw:raw.unlink(missing_ok=True)
        bars+=n;new+=1
        print(f"[stock5m] {i}/{len(selected)} {d}: {n:,} bars",file=sys.stderr,flush=True)
    return {**report,"mode":"backfill","files_new":new,"files_preexisting":old,
            "days_unavailable":missing,"new_5m_bars":bars,
            "location":str(private_path(ROOT)),
            "historical_delisted_tickers_included":True,
            "live_execution_enabled":False}

def status():
    files=list(private_path(ROOT).glob("parquet/session=*/year=*/month=*/*.parquet"))
    return {"parquet_files":len(files),"parquet_gib":round(sum(p.stat().st_size for p in files)/1073741824,3),
            "first":min((p.stem for p in files),default=None),
            "last":max((p.stem for p in files),default=None),
            "research_only":True,"coverage_verified":False}

def probe(symbol,day):
    symbol=symbol.upper().strip()
    if not SYMBOL_RE.fullmatch(symbol):raise ValueError("Invalid stock symbol")
    date.fromisoformat(day)
    api_key=key_from_env()
    url=f"https://api.massive.com/v2/aggs/ticker/{symbol}/range/5/minute/{day}/{day}?"+urlencode(
        {"adjusted":"false","sort":"asc","limit":50000})
    data=get_json(url,key=api_key);rows=data.get("results") or []
    if not isinstance(rows,list) or len(rows)>=50000:raise ValueError("Invalid/truncated REST response")
    return {"ticker":symbol,"date":day,"five_minute_bars":len(rows),
            "source":"Massive Stocks REST","adjusted":False,
            "note":"One-day permission test only; full-market archive requires S3 Flat Files"}
