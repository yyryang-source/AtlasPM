"""Approved source adapters into the common alpha inbox, no separate AI calls."""
import json

from atlaspm.research.alpha_inbox import ingest
from atlaspm.research.options_intelligence import fetch_alerts
from atlaspm.research.news_intelligence import review


def options_once(*, limit=100):
    report=fetch_alerts(limit=limit)
    stamp=report["transport"]["retrieved_at_utc"]
    return sum(ingest(provider="unusual_whales",text=json.dumps(row,ensure_ascii=False,sort_keys=True),
                      symbols=[row["ticker"]],received_at=stamp,ttl=300)
               for row in report["alerts"])


def news_once(provider, month, *, as_of):
    if provider not in {"yahoo_finance","bloomberg"}:
        raise ValueError("Unsupported news source")
    report=review(provider,month,as_of=as_of)
    return sum(ingest(provider=provider,text=json.dumps(row,ensure_ascii=False,sort_keys=True),
                      symbols=row["symbols"],received_at=row["observed_at"],ttl=86400)
               for row in report["events"] if row["fresh"])
