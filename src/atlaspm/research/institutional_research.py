"""Licensed institutional report intake and local, evidence-bound extraction.

No external tools, OCR, model egress or order calls. Input documents remain
in the existing gitignored local_data tree; only bounded revision metadata
and evidence quotes are indexed in the existing research runs area.
"""
from __future__ import annotations

from datetime import UTC, datetime, date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from atlaspm.data.market_db import private_path
from atlaspm.data.provider_policy import RESEARCH_FIRMS, research_rights
from atlaspm.data.sec_download import _atomic_write
from atlaspm.research.financial_events import FLAGS, _digest, _time

ARCHIVE=Path("local_data/runs/institutional_reports.jsonl")
MAX_DOC=8*1024*1024
MAX_ARCHIVE=16*1024*1024
MAX_RECORDS=5000
EVIDENCE_TYPES=frozenset({"original_report","news_relay","rating_summary","ai_inference"})
METRICS={"target price":"target_price","eps":"eps","revenue":"revenue","ebitda":"ebitda",
         "index forecast":"index_forecast","rate forecast":"rate_forecast",
         "macro forecast":"macro_forecast"}
NARRATIVES={"investment thesis","risks","bull case","bear case","valuation",
            "industry outlook","asset allocation","macro outlook"}
_NUMERIC=re.compile(r"^(Target Price|EPS|Revenue|EBITDA|Index Forecast|Rate Forecast|Macro Forecast)"
                    r"\s*\(([A-Z]{3}|PCT|INDEX),\s*(\d{4}-\d{2}-\d{2}),\s*([A-Za-z0-9._-]{1,32})\)"
                    r"\s*:\s*(-?[0-9]{1,18}(?:\.[0-9]{1,8})?)$",re.I)


def _token(value):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9._:/+-]{1,128}",value):
        raise ValueError("Invalid research identifier")
    return value


def _subject(value):
    value=_token(value)
    if not re.fullmatch(r"(?:conid:[1-9][0-9]{0,18}|CIK:[0-9]{10}|ISIN:[A-Z]{2}[A-Z0-9]{10}|index:[A-Za-z0-9._-]+|macro:[A-Za-z0-9._-]+)",value):
        raise ValueError("Stable typed subject required; ticker alone is insufficient")
    return value


def _metadata(row, received):
    fields={"provider","firm","analyst_id","report_id","subject","evidence_type",
            "published_at","first_available_at","source_url","document"}
    if not isinstance(row,dict) or set(row)!=fields:
        raise ValueError("Unexpected institutional metadata fields")
    provider=row["provider"]
    research_rights(provider)
    if row["firm"] not in RESEARCH_FIRMS or row["evidence_type"] not in EVIDENCE_TYPES:
        raise ValueError("Unrecognized firm or evidence type")
    pub=_time(row["published_at"])
    avail=_time(row["first_available_at"]) if row["first_available_at"] is not None else None
    if pub>received or (avail and not pub<=avail<=received):
        raise ValueError("Invalid report time chronology")
    url=row["source_url"]
    if url is not None:
        if not isinstance(url,str) or len(url)>2048 or any(ord(ch)<33 for ch in url):
            raise ValueError("Invalid source URL")
        p=urlsplit(url)
        if p.scheme!="https" or not p.hostname or p.username or p.password or p.query or p.fragment or p.port not in (None,443):
            raise ValueError("Use stable HTTPS URL without credentials or query")
    path=private_path(Path(row["document"]),exists=True)
    return {**row,"analyst_id":_token(row["analyst_id"]),"report_id":_token(row["report_id"]),
            "subject":_subject(row["subject"]),"published_at":pub.isoformat(),
            "first_available_at":avail.isoformat() if avail else None,
            "document":path.relative_to(Path.cwd().resolve()).as_posix()}


def _read_document(path):
    with path.open("rb") as f:
        raw=f.read(MAX_DOC+1)
    if len(raw)>MAX_DOC:
        raise ValueError("Document exceeds 8 MiB")
    digest=sha256(raw).hexdigest()
    if path.suffix.lower()==".txt":
        pages=[raw.decode("utf-8-sig")]
    elif path.suffix.lower()==".pdf":
        if not raw.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF signature")
        from io import BytesIO
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("Install optional institutional-pdf dependency for PDF intake") from None
        reader=PdfReader(BytesIO(raw),strict=True)
        if reader.is_encrypted or len(reader.pages)>100:
            raise ValueError("Encrypted or over-100-page PDF not supported")
        pages=[]
        total=0
        for page in reader.pages:
            # pypdf has no process-level decompression memory guarantee.
            text=page.extract_text() or ""
            total+=len(text)
            if total>500000:
                raise ValueError("Extracted PDF text exceeds limit")
            pages.append(text)
    else:
        raise ValueError("Only authorized UTF-8 text or PDF supported")
    if sum(map(len,pages))>500000 or not any(t.strip() for t in pages):
        raise ValueError("Empty, scanned or oversized document")
    return digest,pages


def extract(pages):
    """Strict tagged-line extraction, not a universal report/table parser.

    Numerical values must be explicit with currency/unit, horizon and basis.
    No inferred estimates from free prose, and no automated instructions.
    """
    claims=[]
    keys=set()
    for page, text in enumerate(pages,1):
        for line in text.splitlines():
            line=line.strip()
            if len(line)>2000:
                continue
            match=_NUMERIC.fullmatch(line)
            claim=None
            if match:
                label,unit,period,basis,value=match.groups()
                metric=METRICS[label.lower()]
                date.fromisoformat(period)
                number=Decimal(value)
                unit=unit.upper()
                if (not number.is_finite() or abs(number)>Decimal("1e18") or
                        (metric=="target_price" and number<=0) or
                        (metric in {"target_price","eps","revenue","ebitda"} and unit in {"PCT","INDEX"}) or
                        (metric=="rate_forecast" and unit!="PCT") or
                        (metric=="index_forecast" and unit!="INDEX")):
                    raise ValueError("Invalid explicit forecast")
                claim={"metric":metric,"unit":unit,"period":period,"basis":basis,"value":str(number)}
            elif re.fullmatch(r"Rating:\s*(Buy|Hold|Sell)",line,re.I):
                claim={"metric":"rating","unit":None,"period":None,"basis":"explicit_buy_hold_sell",
                       "value":line.split(":",1)[1].strip().title()}
            elif re.fullmatch(r"Rating Action:\s*(Upgrade|Downgrade|Initiation|Reiteration|Withdrawal)",line,re.I):
                claim={"metric":"rating_action","unit":None,"period":None,"basis":None,
                       "value":line.split(":",1)[1].strip().lower()}
            elif ":" in line and line.split(":",1)[0].casefold() in NARRATIVES:
                label,value=line.split(":",1)
                if value.strip():
                    claim={"metric":label.lower().replace(" ","_"),"unit":None,"period":None,
                           "basis":None,"value":value.strip()}
            if claim is None:
                continue
            key=(claim["metric"],claim["unit"],claim["period"],claim["basis"])
            if key in keys:
                # Repeated or conflicting tables require operator normalization;
                # never choose a forecast value by page order.
                prior=next(c for c in claims if (c["metric"],c["unit"],c["period"],c["basis"])==key)
                if prior["value"]!=claim["value"]:
                    raise ValueError("Conflicting explicit claim in one report")
                continue
            keys.add(key)
            claims.append({**claim,"page":page,"quote":line,"quote_sha256":sha256(line.encode()).hexdigest()})
            if len(claims)>128:
                raise ValueError("Report claim limit exceeded")
    return claims


def _records(path):
    if not path.exists():
        return []
    if path.stat().st_size>MAX_ARCHIVE:
        raise ValueError("Research archive byte budget exceeded")
    rows=[]
    with path.open(encoding="utf-8") as f:
        for line in f:
            row=json.loads(line)
            payload={k:v for k,v in row.items() if k not in {"revision","received_at"}}
            if _digest(payload)!=row.get("revision"):
                raise ValueError("Research revision integrity failure")
            _time(row["received_at"])
            rows.append(row)
            if len(rows)>MAX_RECORDS:
                raise ValueError("Research record budget exceeded")
    return rows


def ingest(metadata_file, *, archive=ARCHIVE):
    source=private_path(Path(metadata_file),exists=True)
    with source.open("rb") as f:
        raw=f.read(65537)
    if len(raw)>65536:
        raise ValueError("Metadata exceeds 64 KiB")
    received=datetime.now(UTC)
    meta=_metadata(json.loads(raw),received)
    digest,pages=_read_document(private_path(Path(meta["document"]),exists=True))
    claims=extract(pages)
    # Path aliases must not create a new version of identical content.
    payload={**meta,"document_sha256":digest,"claims":claims}
    revision=_digest(payload)
    path=private_path(Path(archive))
    path.parent.mkdir(parents=True,exist_ok=True)
    lock=path.with_suffix(".lock")
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    try:
        rows=_records(path)
        identity=(meta["provider"],meta["firm"],meta["analyst_id"],meta["report_id"],meta["subject"])
        for row in rows:
            if row["revision"]==revision:
                return {**FLAGS,"status":"DUPLICATE","revision":revision,"claim_count":len(claims)}
            other=tuple(row[k] for k in ("provider","firm","analyst_id","report_id","subject"))
            if other==identity and row["document_sha256"]==digest:
                if any(row[k]!=meta[k] for k in ("published_at","first_available_at","evidence_type")):
                    raise ValueError("Same document with conflicting provenance")
                return {**FLAGS,"status":"DUPLICATE_CONTENT","revision":row["revision"],"claim_count":len(row["claims"])}
        rows.append({**payload,"revision":revision,"received_at":received.isoformat()})
        if len(rows)>MAX_RECORDS:
            raise ValueError("Research record budget exceeded")
        body=("\n".join(json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False) for row in rows)+"\n").encode()
        if len(body)>MAX_ARCHIVE:
            raise ValueError("Research archive byte budget exceeded")
        _atomic_write(path,lambda f:f.write(body))
    finally:
        lock.unlink(missing_ok=True)
    return {**FLAGS,"status":"IMPORTED","revision":revision,"claim_count":len(claims),
            "rights_status":"operator_attested_not_independently_verified",
            "parser":"explicit_tagged_lines_v1","document_sha256":digest}


def visible(*, as_of, subject, archive=ARCHIVE):
    """Use actual local receipt and stated availability; do not backdate imports."""
    cutoff=_time(as_of)
    _subject(subject)
    if cutoff>datetime.now(UTC):
        raise ValueError("Future research cutoff")
    selected={}
    for row in _records(private_path(Path(archive))):
        if row["subject"]!=subject or _time(row["received_at"])>cutoff:
            continue
        research_rights(row["provider"])
        key=tuple(row[k] for k in ("provider","firm","analyst_id","report_id","subject"))
        prior=selected.get(key)
        if prior and prior["received_at"]==row["received_at"] and prior["revision"]!=row["revision"]:
            raise ValueError("Conflicting report receipt")
        if not prior or prior["received_at"]<row["received_at"]:
            selected[key]=row
    rows=[]
    for row in selected.values():
        if row["first_available_at"] is not None and _time(row["first_available_at"])>cutoff:
            continue
        digest,pages=_read_document(private_path(Path(row["document"]),exists=True))
        if digest!=row["document_sha256"] or extract(pages)!=row["claims"]:
            raise ValueError("Source report content or parser evidence changed")
        rows.append(row)
    return sorted(rows,key=lambda r:(r["published_at"],r["received_at"],r["revision"]))
