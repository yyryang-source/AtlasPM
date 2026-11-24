"""V5.1 bounded public evidence collection, not historical identity certification."""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from atlaspm.data import hf_stock5m_session_v4 as v4
from atlaspm.data import us_market_universe as universe
from atlaspm.data.hf_stock5m_certification_v5 import ROOT
from atlaspm.data.market_db import private_path

BASE = ROOT / "collector"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
HOSTS = {"www.sec.gov", "www.nasdaqtrader.com", "www.alphavantage.co"}
MAX_BYTES = 100 * 1024 * 1024
FORMS = {"8-K", "8-K/A", "6-K", "6-K/A", "25", "25-NSE", "25/A",
         "15-12B", "15-12G", "15-15D", "S-1", "S-1/A", "F-1", "F-1/A",
         "8-A12B", "8-A12G", "S-4", "F-4"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, data):
    p = private_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def _json(path, obj):
    _write(path, json.dumps(obj, indent=2, sort_keys=True).encode("utf-8"))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Fixed documented HTTPS endpoints only. Never redirect an API key.
        return None


def download(url, agent):
    u = urlsplit(url)
    if u.scheme != "https" or u.netloc not in HOSTS or u.fragment:
        raise ValueError("Unapproved evidence endpoint")
    req = Request(url, headers={"User-Agent": agent, "Accept-Encoding": "identity"})
    try:
        with build_opener(NoRedirect()).open(req, timeout=60) as response:
            data = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        # Do not include request URL (may contain an API key).
        raise ValueError(f"Provider HTTP {code}; collection stopped for this request") from None
    except OSError:
        raise ValueError("Provider connection failed") from None
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Empty or oversized evidence response")
    return data


def quarters(start, end):
    if not re.fullmatch(r"\d{4}-Q[1-4]", start) or not re.fullmatch(r"\d{4}-Q[1-4]", end):
        raise ValueError("Quarter must be YYYY-Q1..Q4")
    today = datetime.now(timezone.utc).date()
    current = f"{today.year}-Q{(today.month-1)//3+1}"
    if start < "1994-Q3" or end >= current or start > end:
        raise ValueError("Only completed SEC quarters from 1994-Q3 are supported")
    y, q = int(start[:4]), int(start[-1])
    while f"{y:04d}-Q{q}" <= end:
        yield f"{y:04d}-Q{q}"
        q += 1
        if q == 5:
            y, q = y+1, 1


def parse_sec_index(data, quarter):
    # Historical SEC indexes may use Latin-1. Preserve the original bytes.
    text = data.decode("latin-1")
    header = "CIK|Company Name|Form Type|Date Filed|Filename"
    lines = iter(StringIO(text))
    for line in lines:
        if line.strip() == header:
            break
    else:
        raise ValueError("SEC master index header missing")
    total, candidates = 0, []
    for line in lines:
        line = line.strip()
        if not line or set(line) == {"-"}:
            continue
        parts = line.split("|")
        if len(parts) != 5:
            raise ValueError("Malformed SEC master index row")
        cik, name, form, filed, path = parts
        d = date.fromisoformat(filed)
        if (not cik.isdigit() or not name or not form
            or f"{d.year}-Q{(d.month-1)//3+1}" != quarter
            or not re.fullmatch(r"edgar/data/\d+/[0-9-]+\.txt", path)):
            raise ValueError("Invalid SEC filing reference")
        total += 1
        if form in FORMS:
            candidates.append({"cik": cik.zfill(10), "issuer": name, "form": form,
                               "filed_date": filed, "source_url": "https://www.sec.gov/Archives/" + path})
    if not total:
        raise ValueError("Empty SEC master index")
    return {"filings": total, "candidate_filings": len(candidates)}, candidates


def parse_sec_tickers(data):
    obj = json.loads(data)
    fields = obj["fields"]
    if not isinstance(fields, list) or len(set(fields)) != len(fields) or not {"cik","name","ticker","exchange"}.issubset(fields):
        raise ValueError("SEC ticker mapping schema changed")
    rows = []
    for row in obj["data"]:
        if len(row) != len(fields):
            raise ValueError("Malformed SEC ticker row")
        r = dict(zip(fields, row))
        if not str(r["cik"]).isdigit() or not r["ticker"]:
            raise ValueError("Invalid SEC ticker row")
        rows.append({k: r[k] for k in ("cik","name","ticker","exchange")})
    if not rows:
        raise ValueError("Empty SEC ticker mapping")
    return {"current_issuer_ticker_rows": len(rows)}, rows


def parse_listing(data):
    reader = csv.DictReader(StringIO(data.decode("utf-8-sig")))
    fields = {"symbol","name","exchange","assetType","ipoDate","delistingDate","status"}
    if not reader.fieldnames or not fields.issubset(reader.fieldnames):
        raise ValueError("Listing CSV unavailable (quota, entitlement or schema)")
    rows = []
    for r in reader:
        if None in r or any(r.get(k) is None for k in fields) or not r["symbol"]:
            raise ValueError("Invalid listing row")
        rows.append({k: r[k] for k in sorted(fields)})
    if not rows:
        raise ValueError("Empty listing snapshot; cannot infer zero securities")
    return {"vendor_listing_rows": len(rows)}, rows


def _nasdaq(data, which, snapshot):
    rows, stats = universe._parse_directory(data.decode("utf-8-sig"), which, snapshot)
    if not rows or stats["file_creation_time"] == "unknown":
        raise ValueError("Incomplete Nasdaq directory")
    return stats, rows


def plan(start, end, listing_dates, current):
    tasks = []
    if current:
        tasks.extend([
            ("sec-current", SEC_TICKERS, "CURRENT_ISSUER_MAPPING"),
            ("nasdaq-current", universe.NASDAQLISTED_URL, "CURRENT_LISTING_SNAPSHOT"),
            ("other-current", universe.OTHERLISTED_URL, "CURRENT_LISTING_SNAPSHOT")])
    for q in quarters(start, end):
        tasks.append(("sec-"+q, f"https://www.sec.gov/Archives/edgar/full-index/{q[:4]}/QTR{q[-1]}/master.idx",
                      "HISTORICAL_FILING_INDEX"))
    for value in sorted(set(listing_dates or [])):
        d = date.fromisoformat(value)
        if d.isoformat() != value or d <= date(2010,1,1) or d > datetime.now(timezone.utc).date():
            raise ValueError("Listing dates must be after 2010-01-01 and not in the future")
        for state in ("active","delisted"):
            query = urlencode({"function":"LISTING_STATUS","date":value,"state":state})
            tasks.append((f"av-{value}-{state}", "https://www.alphavantage.co/query?"+query,
                          "VENDOR_HISTORICAL_LISTING_CANDIDATES"))
    return tasks


def _parse(key, data, retrieved):
    if key == "sec-current":
        return parse_sec_tickers(data)
    if key == "nasdaq-current":
        return _nasdaq(data, "nasdaq", retrieved)
    if key == "other-current":
        return _nasdaq(data, "other", retrieved)
    if key.startswith("sec-"):
        return parse_sec_index(data, key[4:])
    return parse_listing(data)


def _collect(task, agent, key, refresh):
    name, url, scope = task
    # Current snapshots have daily cache keys; old quarters remain reusable.
    day = now()[:10]
    cache_key = name + "-" + day if scope.startswith("CURRENT_") else name
    meta = private_path(BASE / "manifests" / (cache_key+".json"))
    if meta.is_file() and not refresh:
        saved = json.loads(meta.read_text(encoding="utf-8"))
        if saved.get("source_url") != url or saved.get("scope") != scope:
            raise ValueError("Cache manifest source mismatch")
        digest = saved.get("sha256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid cache digest")
        raw = private_path(BASE / "objects" / (digest+".bin"), exists=True)
        data = raw.read_bytes()
        if _sha(data) != digest:
            raise ValueError("Cached evidence hash mismatch")
        retrieved = saved["retrieved_utc"]
        cached = True
    else:
        request_url = url
        if name.startswith("av-"):
            if not key:
                raise ValueError("ALPHAVANTAGE_API_KEY not configured")
            request_url += "&" + urlencode({"apikey":key})
        data = download(request_url, agent)
        retrieved = now()
        digest = _sha(data)
        cached = False
    stats, rows = _parse(name, data, retrieved)
    raw = BASE / "objects" / (digest+".bin")
    normalized = BASE / "candidates" / (cache_key+"-"+digest+".json")
    if not cached:
        _write(raw, data)
    _json(normalized, rows)
    manifest = {"task": name, "scope": scope, "source_url": url,
                "retrieved_utc": retrieved, "sha256": digest,
                "bytes": len(data), "stats": stats, "candidate_path": str(private_path(normalized)),
                "raw_path": str(private_path(raw)), "certified": False}
    _json(meta, manifest)
    return {**manifest, "cached": cached}


def run(start="2000-Q1", end="2026-Q1", *, listing_dates=None, current=True,
        fetch=False, refresh=False, max_requests=0, agent=None):
    tasks = plan(start, end, listing_dates, current)
    if max_requests < 0:
        raise ValueError("max_requests must be nonnegative")
    report = {"schema":"atlaspm_hf_evidence_collector_v51", **v4.GUARDS,
              "local_only": not fetch, "mode":"fetch" if fetch else "plan",
              "tasks_planned":len(tasks), "tasks":[{"task":n,"source_url":u,"scope":s} for n,u,s in tasks],
              "results":[], "requests_attempted_upper_bound":0,
              "warning":"Candidates only; no stable security IDs, corporate actions, historical completeness or rights inferred."}
    if not fetch:
        return report
    agent = agent or os.environ.get("ATLASPM_SEC_USER_AGENT", "")
    if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", agent) or "\n" in agent or "\r" in agent:
        raise ValueError("Set ATLASPM_SEC_USER_AGENT to your organization/name and contact email")
    key = os.environ.get("ALPHAVANTAGE_API_KEY")
    # Serial requests; conservative SEC pacing. Stop on provider/validation error
    # rather than bypass rate limits. Count every attempted task against budget.
    for task in tasks:
        if max_requests and report["requests_attempted_upper_bound"] >= max_requests:
            break
        report["requests_attempted_upper_bound"] += 1
        try:
            item = _collect(task, agent, key, refresh)
            report["results"].append(item)
            if not item["cached"]:
                report["network_downloads"] += 1
            time.sleep(0.25 if not task[0].startswith("av-") else 1.0)
        except Exception as exc:
            report["results"].append({"task":task[0], "error":type(exc).__name__,
                                      "detail":str(exc) if isinstance(exc, ValueError) else "Collection failed"})
            break
    report["tasks_completed"] = sum("sha256" in x for x in report["results"])
    report["status"] = ("CANDIDATES_COLLECTED" if report["tasks_completed"] == len(tasks)
                        else "PARTIAL_OR_BLOCKED")
    report["summary_path"] = str(private_path(BASE / "latest.json"))
    _json(BASE / "latest.json", report)
    return report
