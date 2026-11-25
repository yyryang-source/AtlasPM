"""Federal Reserve official RSS press metadata using shared Gateway."""
from __future__ import annotations
from datetime import UTC
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree.ElementTree import fromstring
from atlaspm.data.api_gateway import DEFAULT_GATEWAY, GatewayError
from atlaspm.research.financial_events import normalize, _digest

BASE="https://www.federalreserve.gov"


def recent_events(*, monetary_only=True, gateway=DEFAULT_GATEWAY):
    if type(monetary_only) is not bool:
        raise ValueError("Expected boolean feed choice")
    response=gateway.fetch("federal_reserve",BASE+("/feeds/press_monetary.xml"
                           if monetary_only else "/feeds/press_all.xml"))
    xml=response.body.decode("utf-8-sig")
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise GatewayError("RSS declarations forbidden")
    items=fromstring(xml).findall("./channel/item")
    if len(items)>1000:
        raise GatewayError("RSS item budget exceeded")
    events=[]
    metadata=[]
    for item in items:
        title,link,pub=(item.findtext(k) for k in ("title","link","pubDate"))
        if not isinstance(title,str) or not 1<=len(title)<=1000 or not isinstance(link,str) or len(link)>2048:
            raise GatewayError("Invalid Federal Reserve RSS metadata")
        p=urlsplit(link)
        if p.scheme!="https" or p.netloc!="www.federalreserve.gov":
            raise GatewayError("Unexpected Federal Reserve article destination")
        try:
            stamp=parsedate_to_datetime(pub)
            if stamp.utcoffset() is None:
                raise ValueError("Missing offset")
        except (ValueError,TypeError,AttributeError):
            raise GatewayError("Invalid Federal Reserve RSS publication date") from None
        row={"title":title,"url":link,"published_at":stamp.astimezone(UTC).isoformat()}
        digest=_digest(row)
        metadata.append({**row,"evidence_sha256":digest})
        # Monetary policy announcements are not necessarily rate decisions.
        kind="rate_decision" if "fomc statement" in title.casefold() else "macro_release"
        events.append(normalize({"provider":"federal_reserve","source_id":_digest(link),
            "kind":kind,"subjects":[],"published_at":row["published_at"],
            "first_available_at":None,"effective_at":None,"evidence_sha256":digest,
            "delivery":"poll","expiry_seconds":86400},received_at=response.fetched_at))
    return {"events":events,"metadata":metadata,"transport":response.evidence(),
            "coverage_complete":False,"external_ai_allowed":False}
