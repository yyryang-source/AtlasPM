"""Bounded disclosure document storage and local text extraction."""
from datetime import UTC, datetime
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import json
import re
from urllib.parse import urlsplit

from atlaspm.data.api_gateway import DEFAULT_GATEWAY, GatewayError
from atlaspm.data.market_db import private_path
from atlaspm.data.sec_download import _atomic_write

ROOT = Path("local_data/runs/disclosure_documents")
MAX_TEXT = 24000


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []
        self.size = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "ix:hidden"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "ix:hidden"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden and self.size < MAX_TEXT:
            text = " ".join(data.split())[:MAX_TEXT-self.size]
            if text:
                self.parts.append(text)
                self.size += len(text) + 1


def document_url(source, row):
    if source == "sec":
        accession, cik = row["accession"], row["archive_cik"]
        if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession) or not re.fullmatch(r"[0-9]{10}", cik):
            raise ValueError("Invalid SEC identity")
        return "sec_document", (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                               f"{accession.replace('-', '')}/{accession}.txt")
    if source == "fed":
        p = urlsplit(row["url"])
        if p.scheme != "https" or p.netloc != "www.federalreserve.gov" or p.query or p.fragment or not re.fullmatch(r"/newsevents/pressreleases/[A-Za-z0-9_-]+\.htm", p.path):
            raise ValueError("Unsupported Fed document route")
        return "fed_document", row["url"]
    raise ValueError("Unknown disclosure source")


def capture(source, row, *, root=ROOT, gateway=DEFAULT_GATEWAY):
    provider, url = document_url(source, row)
    identity = sha256(url.encode()).hexdigest()
    folder = private_path(Path(root))
    folder.mkdir(parents=True, exist_ok=True)
    receipt_path = private_path(folder / (identity + ".json"))
    if receipt_path.exists():
        if receipt_path.stat().st_size > 256 * 1024:
            raise GatewayError("Oversized disclosure receipt")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        digest = receipt["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise GatewayError("Invalid disclosure digest")
        raw_path = private_path(folder / (digest + ".raw"))
        if raw_path.stat().st_size > 32 * 1024 * 1024 or sha256(raw_path.read_bytes()).hexdigest() != digest:
            raise GatewayError("Disclosure archive integrity failure")
        if receipt["url"] != url:
            raise GatewayError("Disclosure receipt identity mismatch")
        return receipt
    response = gateway.fetch(provider, url)
    raw = response.body
    digest = sha256(raw).hexdigest()
    raw_path = private_path(folder / (digest + ".raw"))
    if not raw_path.exists():
        _atomic_write(raw_path, lambda f: f.write(raw))
    parser = VisibleText()
    parser.feed(raw.decode("utf-8", errors="replace"))
    text = " ".join(parser.parts)[:MAX_TEXT]
    if not text:
        raise GatewayError("No extractable disclosure text")
    receipt = {"source": source, "url": url, "sha256": digest,
               "retrieved_at": response.fetched_at, "stored_at": datetime.now(UTC).isoformat(),
               "text": text, "text_is_bounded_excerpt": True,
               "research_only": True, "live_execution_enabled": False}
    _atomic_write(receipt_path, lambda f: f.write(json.dumps(receipt, ensure_ascii=False).encode()))
    return receipt
