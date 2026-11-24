"""V5.2: local indexed SEC filing candidates and bounded evidence extraction.

Issuer-level text hints are not security-level corporate actions or PIT truth.
Never promote a regex result or an SEC CIK into a certified identity ledger.
"""
from __future__ import annotations

from datetime import date
from html.parser import HTMLParser
from fractions import Fraction
import json
import os
import re
import time

from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data import hf_stock5m_session_v4 as v4
from atlaspm.data.hf_stock5m_certification_v5 import ROOT
from atlaspm.data.market_db import private_path

BASE = ROOT / "filings_v52"
VERSION = "v52.1"
URL = re.compile(r"https://www\.sec\.gov/Archives/edgar/data/[0-9]+/[0-9-]+\.txt")
KINDS = {
    "split": ("8-K", "8-K/A", "6-K", "6-K/A"),
    "exit": ("25", "25-NSE", "25/A", "15-12B", "15-12G", "15-15D", "8-K", "8-K/A", "6-K", "6-K/A"),
    "listing": ("S-1", "S-1/A", "F-1", "F-1/A", "8-A12B", "8-A12G"),
    "merger": ("S-4", "F-4", "8-K", "8-K/A", "6-K", "6-K/A"),
    "ticker": ("8-K", "8-K/A", "6-K", "6-K/A"),
    "dividend": ("8-K", "8-K/A", "6-K", "6-K/A"),
}
# High-recall cues only. A hit may describe an announcement, proposal,
# historical event or a different security. Do not infer effective dates.
PATTERNS = {
    "SPLIT_RATIO": re.compile(r"\b(\d{1,7})\s*[-– ]\s*(?:for|to)\s*[-– ]\s*(\d{1,7})\b", re.I),
    "SPLIT": re.compile(r"\b(?:reverse\s+)?(?:stock|share)\s+split\b", re.I),
    "DELISTING": re.compile(r"\b(?:delist(?:ing|ed)?|withdrawal from listing|removal from listing)\b", re.I),
    "LISTING": re.compile(r"\b(?:initial public offering|approved for listing|commence trading)\b", re.I),
    "MERGER": re.compile(r"\b(?:merger agreement|completed the merger|cash consideration|plan of merger)\b", re.I),
    "TICKER_CHANGE": re.compile(r"\b(?:change (?:in|of) (?:its |the )?(?:ticker|trading) symbol|new (?:ticker|trading) symbol|trading symbol (?:will change|changed))\b", re.I),
    "DIVIDEND": re.compile(r"\b(?:cash dividend|dividend of|distribution of \$)\b", re.I),
}
MAX_HINTS = 100


def _db():
    path = private_path(BASE / "index.duckdb")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import duckdb
    except ImportError as exc:
        raise ValueError('Install lake extras: pip install -e ".[lake]"') from exc
    cx = duckdb.connect(str(path))
    try:
        cx.execute("""CREATE TABLE IF NOT EXISTS partitions(
            quarter VARCHAR PRIMARY KEY, digest VARCHAR NOT NULL,
            parser_version VARCHAR NOT NULL, candidate_rows BIGINT NOT NULL,
            rejected_rows BIGINT NOT NULL)""")
        cx.execute("""CREATE TABLE IF NOT EXISTS filings(
            quarter VARCHAR NOT NULL, cik VARCHAR NOT NULL, issuer VARCHAR NOT NULL,
            form VARCHAR NOT NULL, filed_date DATE NOT NULL, source_url VARCHAR NOT NULL,
            PRIMARY KEY(quarter,cik,form,filed_date,source_url))""")
    except Exception:
        cx.close()
        raise
    return cx


def _load_partition(cx, quarter):
    task = "sec-" + quarter
    meta = private_path(ev.BASE / "manifests" / (task+".json"), exists=True)
    m = json.loads(meta.read_text(encoding="utf-8"))
    expected = f"https://www.sec.gov/Archives/edgar/full-index/{quarter[:4]}/QTR{quarter[-1]}/master.idx"
    digest = m.get("sha256", "")
    if (m.get("task") != task or m.get("source_url") != expected
            or m.get("scope") != "HISTORICAL_FILING_INDEX"
            or not re.fullmatch("[0-9a-f]{64}", digest)):
        raise ValueError("Invalid source manifest")
    raw = private_path(ev.BASE / "objects" / (digest+".bin"), exists=True)
    data = raw.read_bytes()
    if ev._sha(data) != digest:
        raise ValueError("Quarter source hash mismatch")
    prev = cx.execute("SELECT digest,parser_version,candidate_rows,rejected_rows FROM partitions WHERE quarter=?",
                      [quarter]).fetchone()
    if prev and prev[:2] == (digest, VERSION):
        actual = cx.execute("SELECT count(*) FROM filings WHERE quarter=?", [quarter]).fetchone()[0]
        if actual == prev[2]:
            return {"quarter":quarter,"status":"REUSED","candidate_rows":actual,"rejected_rows":prev[3]}
    stats, rows = ev.parse_sec_index(data, quarter)
    # Import trusted reparse of raw bytes, never mutable candidate JSON.
    normalized = private_path(BASE / "import" / (quarter+".json"))
    ev._json(normalized, rows)
    cx.execute("BEGIN TRANSACTION")
    try:
        cx.execute("DELETE FROM filings WHERE quarter=?", [quarter])
        cx.execute("""INSERT INTO filings
            SELECT DISTINCT ?,cik,issuer,form,CAST(filed_date AS DATE),source_url
            FROM read_json(?, columns={cik:'VARCHAR',issuer:'VARCHAR',form:'VARCHAR',
                                      filed_date:'VARCHAR',source_url:'VARCHAR'}, format='array')""",
                   [quarter,str(normalized)])
        count = cx.execute("SELECT count(*) FROM filings WHERE quarter=?", [quarter]).fetchone()[0]
        cx.execute("DELETE FROM partitions WHERE quarter=?", [quarter])
        cx.execute("INSERT INTO partitions VALUES (?,?,?,?,?)",
                   [quarter,digest,VERSION,count,stats.get("rejected_rows",0)])
        cx.execute("COMMIT")
    except Exception:
        cx.execute("ROLLBACK")
        raise
    finally:
        normalized.unlink(missing_ok=True)
    return {"quarter":quarter,"status":"INDEXED","candidate_rows":count,
            "duplicate_index_rows":len(rows)-count,"rejected_rows":stats.get("rejected_rows",0)}


def build(start="2000-Q1", end="2026-Q1"):
    chosen = list(ev.quarters(start,end))
    results = []
    cx = _db()
    try:
        for quarter in chosen:
            try:
                results.append(_load_partition(cx,quarter))
            except Exception as exc:
                results.append({"quarter":quarter,"status":"ERROR","detail":str(exc)})
    finally:
        cx.close()
    report = {"schema":"atlaspm_sec_filing_index_v52",**v4.GUARDS,
              "quarters_selected":len(chosen),
              "quarters_completed":sum(r["status"]!="ERROR" for r in results),
              "candidate_rows":sum(r.get("candidate_rows",0) for r in results),
              "rejected_rows":sum(r.get("rejected_rows",0) for r in results),
              "quarters":results}
    report["status"] = ("INDEX_READY_FOR_RESEARCH" if report["quarters_completed"]==len(chosen)
                        else "PARTIAL_INDEX")
    report["summary_path"] = str(private_path(BASE / "index_report.json"))
    ev._json(BASE / "index_report.json",report)
    return report


def _iso(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat()!=value:
        raise ValueError("Use ISO dates YYYY-MM-DD")
    return parsed


def select(ciks, start, end, kind, limit):
    if not ciks or any(not re.fullmatch(r"[0-9]{1,10}", str(c)) for c in ciks):
        raise ValueError("Explicit SEC issuer CIKs required; no current-ticker backdating")
    if _iso(start)>_iso(end) or kind not in KINDS or not 1<=limit<=500:
        raise ValueError("Invalid date range, event kind or limit (1..500)")
    ids = sorted({str(c).zfill(10) for c in ciks})
    forms = KINDS[kind]
    cx = _db()
    try:
        rows = cx.execute(
            "SELECT cik,issuer,form,filed_date,source_url FROM filings "
            "WHERE cik IN (SELECT unnest(?)) AND form IN (SELECT unnest(?)) "
            "AND filed_date BETWEEN ? AND ? ORDER BY filed_date,cik,form,source_url LIMIT ?",
            [ids,list(forms),start,end,limit+1]).fetchall()
        indexed = [r[0] for r in cx.execute("SELECT quarter FROM partitions ORDER BY quarter").fetchall()]
    finally:
        cx.close()
    first, last = _iso(start), _iso(end)
    expected = [f"{year:04d}-Q{q}" for year in range(first.year,last.year+1)
                for q in range(1,5) if (first.year,(first.month-1)//3+1)
                <= (year,q) <= (last.year,(last.month-1)//3+1)]
    return {"schema":"atlaspm_sec_filing_selection_v52",**v4.GUARDS,
            "missing_requested_quarters":sorted(set(expected)-set(indexed)),
            "kind":kind,"indexed_quarters":indexed,"truncated":len(rows)>limit,
            "filings":[{"cik":c,"issuer":i,"form":f,"filed_date":d.isoformat(),"source_url":u}
                       for c,i,f,d,u in rows[:limit]]}


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script","style"):
            self.skip += 1
        elif tag in ("p","div","br","tr","td","li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script","style") and self.skip:
            self.skip -= 1
        elif tag in ("p","div","tr","td","li"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def extract(data, filing):
    """Extract bounded auditable snippets, never a certified event/date."""
    text = data.decode("utf-8",errors="replace")
    # SEC complete submission headers: do not accept HTML error pages.
    if not re.search(r"<(?:SEC-DOCUMENT|IMS-DOCUMENT|SEC-HEADER)>",text[:65536],re.I):
        raise ValueError("Missing SEC submission header")
    # Exclude encoded graphics and exhibits; only primary form text is read.
    docs = re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>",text,re.I|re.S)
    selected = []
    for doc in docs:
        form = re.search(r"(?im)^\s*<TYPE>\s*([^\r\n<]+)",doc)
        if form and form.group(1).strip().upper()==filing["form"].upper():
            selected.append(doc)
    if not selected:
        raise ValueError("Primary form not found; no exhibit-only inference")
    parser = TextParser()
    for doc in selected:
        parser.feed(doc)
        parser.parts.append(" ")
    parser.close()
    clean = re.sub(r"\s+"," ","".join(parser.parts))
    hints = []
    for kind, pattern in PATTERNS.items():
        for m in pattern.finditer(clean):
            if kind=="SPLIT_RATIO" and not PATTERNS["SPLIT"].search(clean[max(0,m.start()-100):m.end()+100]):
                continue
            hint = {"kind":kind,"matched":m.group(0),"offset":m.start(),
                    "excerpt":clean[max(0,m.start()-160):m.end()+160],
                    "known_date_lower_bound":filing["filed_date"],
                    "effective_date":None,"security_id":None,"status":"UNVERIFIED_TEXT_HINT"}
            if kind=="SPLIT_RATIO":
                hint["ratio_terms"] = [int(m.group(1)),int(m.group(2))]
            hints.append(hint)
            if len(hints)>=MAX_HINTS:
                return _hint_summary(hints, True)
    return _hint_summary(hints, False)


def _hint_summary(hints, truncated):
    ratios = {Fraction(*h["ratio_terms"]) for h in hints if h["kind"]=="SPLIT_RATIO"
              and min(h["ratio_terms"])>0}
    return {"hints":hints,"truncated":truncated,
            "multiple_ratio_candidates":len(ratios)>1}


def collect(ciks,start,end,kind,limit=20,*,fetch=False,max_requests=20):
    if not 1<=max_requests<=500:
        raise ValueError("Request budget must be 1..500")
    report = select(ciks,start,end,kind,limit)
    report["schema"]="atlaspm_sec_filing_evidence_v52"
    report["local_only"]=not fetch
    report["results"]=[]
    report["requests_attempted"]=0
    agent = os.environ.get("ATLASPM_SEC_USER_AGENT","")
    if fetch and (not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+",agent) or "\n" in agent or "\r" in agent):
        raise ValueError("Set ATLASPM_SEC_USER_AGENT to name and real contact email")
    for filing in report["filings"]:
        url = filing["source_url"]
        if not URL.fullmatch(url):
            report["results"].append({**filing,"error":"UNSAFE_SOURCE_URL"})
            continue
        key = ev._sha(url.encode())
        manifest = private_path(BASE / "body_manifests" / (key+".json"))
        try:
            if manifest.is_file():
                m = json.loads(manifest.read_text(encoding="utf-8"))
                digest = m.get("sha256","")
                if m.get("source_url")!=url or not re.fullmatch("[0-9a-f]{64}",digest):
                    raise ValueError("Invalid body manifest")
                raw = private_path(BASE / "bodies" / (digest+".bin"),exists=True)
                data = raw.read_bytes()
                if ev._sha(data)!=digest:
                    raise ValueError("Body cache hash mismatch")
            elif not fetch or report["requests_attempted"]>=max_requests:
                report["results"].append({**filing,"status":"NOT_CACHED"})
                continue
            else:
                report["requests_attempted"]+=1
                try:
                    data = ev.download(url,agent)
                except ValueError as exc:
                    report["results"].append({**filing,"error":str(exc)})
                    break  # access restriction or connection failure: stop network
                finally:
                    time.sleep(0.5)
                report["network_downloads"]+=1
                digest = ev._sha(data)
                ev._write(BASE / "bodies" / (digest+".bin"),data)
                ev._json(manifest,{"source_url":url,"sha256":digest,"retrieved_utc":ev.now()})
            result = extract(data,filing)
            item = {**filing,"sha256":digest,**result,"status":"REVIEW_REQUIRED"}
            ev._json(BASE / "hints" / (key+"-"+digest+"-"+filing["form"].replace("/","_")+".json"),item)
            report["results"].append({**filing,"sha256":digest,
                                     "hint_count":len(result["hints"]),"truncated":result["truncated"],
                                     "multiple_ratio_candidates":result["multiple_ratio_candidates"],
                                     "status":"REVIEW_REQUIRED"})
        except Exception as exc:
            report["results"].append({**filing,"error":str(exc)})
    report["filings_parsed"]=sum("hint_count" in x for x in report["results"])
    report["hints_count"]=sum(x.get("hint_count",0) for x in report["results"])
    report["status"]="EVIDENCE_REVIEW_REQUIRED"
    report["summary_path"]=str(private_path(BASE / "evidence_report.json"))
    ev._json(BASE / "evidence_report.json",report)
    return report
