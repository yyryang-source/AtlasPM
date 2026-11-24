"""Download bounded official US Treasury auctions and permissioned Massive options CSVs.

All files remain under ignored local_data/. Downloads are never trading signals.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

from atlaspm.data.market_db import dt, option_id, private_path
from atlaspm.data.api_gateway import DEFAULT_GATEWAY

FISCAL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/treasury_securities_auctions_v2"
MASSIVE = "https://api.massive.com"
NY = ZoneInfo("America/New_York")
TCOLS = ("cusip", "security_type", "security_term", "auction_date", "issue_date",
         "maturity_date", "interest_rate", "high_yield", "offering_amt")
CCOLS = ("ticker", "underlying_ticker", "expiration_date", "contract_type", "strike_price")
BCOLS = ("symbol", "bar", "datetime_et", "date_et", "timestamp_ms", "open", "high", "low",
         "close", "volume_contracts", "vwap", "trade_count", "sma5", "sma10", "sma20",
         "ema5", "ema10", "ema20", "volume_ma20", "consecutive_bars")


def get_json(url: str, key: str | None = None) -> dict:
    """Existing CSV downloaders share provider transport and limits."""
    provider = "massive" if key else "treasury"
    return DEFAULT_GATEWAY.fetch(provider, url, key=key).data()


def save_csv(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    dest = private_path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(".csv.part")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, dest)
    finally:
        temporary.unlink(missing_ok=True)


def fiscal_row(item: dict) -> dict:
    def pick(*keys):
        for k in keys:
            v = item.get(k)
            if v is not None and str(v).strip() not in ("", "null", "N/A"):
                return v
        return ""
    return {
        "cusip": pick("cusip"), "security_type": pick("security_type"),
        "security_term": pick("security_term", "security_desc"),
        "auction_date": pick("auction_date", "comp_auction_close_date"),
        "issue_date": pick("issue_date"), "maturity_date": pick("maturity_date"),
        "interest_rate": pick("interest_rate", "int_rate"),
        "high_yield": pick("high_yield"), "offering_amt": pick("offering_amt"),
    }


def treasury(*, max_pages: int = 0, refresh: bool = False, delay: float = 0.5,
             output_dir: Path = Path("local_data/bonds/treasury/fiscaldata_v2")) -> dict:
    if not (0 <= max_pages <= 10000 and delay >= 0.2):
        raise ValueError("max_pages 0..10000 and delay >=0.2 required")
    directory = private_path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    fetched = reused = rows_seen = 0
    finished = False
    for page in range(1, (max_pages or 10000) + 1):
        file = directory / f"auctions_{page:04}.csv"
        if file.exists() and not refresh:
            with file.open("r", encoding="utf-8-sig", newline="") as f:
                n = sum(1 for _ in csv.DictReader(f))
            reused += 1
            rows_seen += n
        else:
            if fetched:
                time.sleep(delay)
            url = FISCAL + "?" + urlencode({
                "sort": "-record_date", "page[number]":page,"page[size]":1000})
            result = get_json(url)
            rows = result.get("data", [])
            if not isinstance(rows, list):
                raise ValueError("FiscalData response has no data array")
            if not rows:
                finished = True
                break
            n = len(rows)
            save_csv(file, TCOLS, [fiscal_row(x) for x in rows])
            fetched += 1
            rows_seen += n
        if n < 1000:
            finished = True
            break
    return {"fetched_pages":fetched,"cached_pages":reused,"csv_rows_seen":rows_seen,
            "download_complete_by_page_size":finished,
            "location":str(directory),"research_only":True,
            "warning":"Treasury auction history, not executable secondary-market prices"}


def key_from_env() -> str:
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not key:
        raise ValueError("MASSIVE_API_KEY not set. Obtain a key; do not paste it into chat.")
    return key


def contracts(*, underlyings: list[str], as_of: str,
              expiry: str | None = None, max_contracts: int = 20, rpm: int = 5,
              output: Path = Path("local_data/options/massive/selected_contracts.csv")) -> dict:
    key = key_from_env()
    as_of = dt(as_of, "as_of")
    expiry = dt(expiry, "expiry", optional=True)
    if not underlyings or not (1 <= max_contracts <= 10000) or not (1 <= rpm <= 60):
        raise ValueError("Underlyings, max_contracts 1..10000, rpm 1..60 required")
    out, count_requests = [], 0
    for under in underlyings:
        under = under.upper().strip()
        if not under.replace(".", "").isalpha() or len(under) > 8:
            raise ValueError(f"Invalid underlying: {under!r}")
        query = {"underlying_ticker":under, "as_of":as_of,
                 "expiration_date.gte":as_of, "limit":1000,
                 "sort":"expiration_date","order":"asc"}
        if expiry:
            query["expiration_date"] = expiry
        url = MASSIVE + "/v3/reference/options/contracts?" + urlencode(query)
        seen, collected, pages = set(), 0, 0
        while url and collected < max_contracts and pages < 50:
            if count_requests:
                time.sleep(60 / rpm)
            data = get_json(url, key=key)
            pages += 1
            count_requests += 1
            for item in data.get("results", []):
                sym = item.get("ticker")
                try:
                    option_id(sym)
                except (ValueError, TypeError):
                    continue
                if (sym in seen or str(item.get("underlying_ticker") or "").upper() != under
                        or not item.get("expiration_date") or not item.get("strike_price")):
                    continue
                seen.add(sym)
                out.append({c:item.get(c,"") for c in CCOLS})
                collected += 1
                if collected >= max_contracts:
                    break
            url = data.get("next_url")
            if url and (urlparse(url).scheme, urlparse(url).netloc) != ("https", "api.massive.com"):
                raise ValueError("Unexpected contract pagination host")
    if not out:
        raise ValueError("No contracts returned, existing CSV is untouched")
    save_csv(output, CCOLS, out)
    return {"contracts_downloaded":len(out), "api_requests":count_requests,
            "file":str(private_path(output)),"as_of":as_of,
            "research_only":True,"warning":"Current/reference contracts only; no option price bars yet"}


def with_ma(bars: list[dict], timeframe: str) -> list[dict]:
    """SMA/EMA in bar units, reset upon intraday gaps; never forward-fill prices."""
    last, prices, volumes = None, [], []
    emas = {5:None,10:None,20:None}
    step = {"1d":None,"5m":300000,"1m":60000}[timeframe]
    out = []
    for raw in sorted(bars,key=lambda item:int(item["timestamp_ms"])):
        t = int(raw["timestamp_ms"])
        if step and (last is None or t-last != step):
            prices, volumes, emas = [],[],{5:None,10:None,20:None}
        last = t
        row = dict(raw)
        prices.append(float(row["close"]))
        volumes.append(float(row["volume_contracts"]))
        for n in (5,10,20):
            if len(prices) >= n:
                row[f"sma{n}"] = sum(prices[-n:])/n
                alpha = 2/(n+1)
                emas[n] = (sum(prices[:n])/n if emas[n] is None
                           else prices[-1]*alpha+emas[n]*(1-alpha))
                row[f"ema{n}"] = emas[n]
            else:
                row[f"sma{n}"] = row[f"ema{n}"] = ""
        row["volume_ma20"] = sum(volumes[-20:])/20 if len(volumes)>=20 else ""
        row["consecutive_bars"] = len(prices)
        out.append(row)
    return out


def bars(*, contracts_file: Path, start: str, end: str,
         timeframes: list[str], max_contracts: int = 10, rpm: int = 5,
         refresh: bool = False,
         output_dir: Path = Path("local_data/options/massive")) -> dict:
    key = key_from_env()
    start, end = dt(start,"start"),dt(end,"end")
    if start > end:
        raise ValueError("start must not follow end")
    if not timeframes or any(tf not in ("1d","5m","1m") for tf in timeframes):
        raise ValueError("timeframes must be 1d, 5m or 1m")
    if not (1 <= max_contracts <= 100000 and 1 <= rpm <= 60):
        raise ValueError("Invalid max_contracts or rpm")
    root=private_path(output_dir)
    with private_path(contracts_file,exists=True).open("r",encoding="utf-8-sig",newline="") as f:
        symbols = list(dict.fromkeys(row["ticker"] for row in csv.DictReader(f)))[:max_contracts]
    requested=downloaded=reused=empty=0
    for sym in symbols:
        option_id(sym)
        for tf in timeframes:
            file=root/tf/(sym.replace(":","_")+".csv")
            if file.exists() and not refresh:
                reused+=1
                continue
            if requested:
                time.sleep(60/rpm)
            n, unit=(1,"day") if tf=="1d" else (int(tf[:-1]),"minute")
            url=(f"{MASSIVE}/v2/aggs/ticker/{sym}/range/{n}/{unit}/{start}/{end}?"
                 +urlencode({"adjusted":"true","sort":"asc","limit":50000}))
            data=get_json(url,key=key)
            requested+=1
            raw=data.get("results") or []
            if not isinstance(raw,list):
                raise ValueError("Unexpected options aggregate response")
            if not raw:
                empty+=1
                continue
            if len(raw)>=50000:
                raise ValueError("Aggregate response may be truncated: narrow your date range")
            result=[]
            for entry in raw:
                timestamp=int(entry["t"])
                when=datetime.fromtimestamp(timestamp/1000,tz=timezone.utc).astimezone(NY)
                result.append({"symbol":sym,"bar":tf,"datetime_et":when.isoformat(),
                    "date_et":when.date().isoformat(),"timestamp_ms":timestamp,
                    "open":entry["o"],"high":entry["h"],"low":entry["l"],"close":entry["c"],
                    "volume_contracts":entry.get("v",0),"vwap":entry.get("vw",""),
                    "trade_count":entry.get("n","")})
            save_csv(file,BCOLS,with_ma(result,tf))
            downloaded+=1
    return {"api_requests":requested,"csv_files_downloaded":downloaded,
            "cached_files_reused":reused,"no_trade_files_skipped":empty,
            "output_directory":str(root),"research_only":True,
            "warning":"Only selected contracts, not all OPRA history; no missing bars fabricated"}
