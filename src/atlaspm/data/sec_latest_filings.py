"""Near-real-time public disclosure discovery; no trade or external AI egress."""
from datetime import UTC, datetime
from urllib.parse import urlsplit
from xml.etree.ElementTree import fromstring
import re

from atlaspm.data.api_gateway import DEFAULT_GATEWAY, GatewayError
from atlaspm.research.financial_events import normalize, _digest

URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type="
       "&company=&dateb=&owner=include&start=0&count=100&output=atom")
NS = {"a": "http://www.w3.org/2005/Atom"}
INDEX = re.compile(r"/Archives/edgar/data/([0-9]{1,10})/([0-9]{18})/([0-9]{10}-[0-9]{2}-[0-9]{6})-index\.html?")


def recent_filings(*, gateway=DEFAULT_GATEWAY):
    response = gateway.fetch("sec_feed", URL)
    root = fromstring(response.body)
    entries = root.findall("a:entry", NS)
    if len(entries) > 100:
        raise GatewayError("SEC feed entry limit")
    events, metadata = [], []
    for entry in entries:
        title = entry.findtext("a:title", namespaces=NS)
        updated = entry.findtext("a:updated", namespaces=NS)
        links = [n.get("href", "") for n in entry.findall("a:link", NS)
                 if n.get("rel", "alternate") == "alternate"]
        if not title or len(title) > 1000 or not updated or len(links) != 1:
            raise GatewayError("SEC feed entry schema")
        link = urlsplit(links[0])
        match = INDEX.fullmatch(link.path)
        if link.scheme != "https" or link.netloc != "www.sec.gov" or link.query or link.fragment or not match:
            raise GatewayError("SEC filing link scope")
        cik, compact, accession = match.groups()
        if compact != accession.replace("-", "") or int(cik) == 0:
            raise GatewayError("SEC accession mismatch")
        stamp = datetime.fromisoformat(updated)
        if stamp.utcoffset() is None:
            raise GatewayError("SEC feed timestamp requires offset")
        row = {"accession": accession, "archive_cik": cik.zfill(10),
               "title": title, "url": links[0],
               "feed_updated_at": stamp.astimezone(UTC).isoformat()}
        digest = _digest(row)
        # Archive CIK is not necessarily the issuer. Resolve identities separately.
        events.append(normalize({"provider": "sec", "source_id": accession,
            "kind": "sec_disclosure", "subjects": [], "published_at": None,
            "first_available_at": None, "effective_at": None,
            "evidence_sha256": digest, "delivery": "poll", "expiry_seconds": 86400},
            received_at=response.fetched_at))
        metadata.append(row)
    return {"events": events, "metadata": metadata, "transport": response.evidence(),
            "coverage_complete": False}
