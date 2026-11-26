"""Approved source adapters into the common alpha inbox, no separate AI calls."""
import json
from atlaspm.research.financial_events import _digest

from atlaspm.research.alpha_inbox import ingest
from atlaspm.research.options_intelligence import fetch_alerts
from atlaspm.research.news_intelligence import review


def options_once(*, limit=100):
    report=fetch_alerts(limit=limit)
    stamp=report["transport"]["retrieved_at_utc"]
    return sum(ingest(provider="unusual_whales",text=json.dumps(row,ensure_ascii=False,sort_keys=True),
                      symbols=[row["ticker"]],received_at=stamp,ttl=300,
                      source_revision=_digest(row))
               for row in report["alerts"])


def news_once(provider, month, *, as_of):
    if provider not in {"yahoo_finance","bloomberg"}:
        raise ValueError("Unsupported news source")
    report=review(provider,month,as_of=as_of)
    return sum(ingest(provider=provider,text=json.dumps(row,ensure_ascii=False,sort_keys=True),
                      symbols=row["symbols"],received_at=row["observed_at"],ttl=86400,
                      source_revision=row["evidence_id"])
               for row in report["events"] if row["fresh"])


def market_observation(identity, history, quote, *, asset_type, as_of):
    """Reuse validated technical metrics and broker quote diagnostics, not a second AI."""
    from atlaspm.research.intelligence import broker_market_research
    report = broker_market_research(identity, history, quote, asset_type=asset_type, as_of=as_of)
    if report["status"] != "DESCRIPTIVE_RESEARCH_COMPLETED":
        return 0
    from atlaspm.research.alpha_technical import metrics
    from atlaspm.research.financial_events import _time
    received_ms = int(_time(history["retrieved_at_utc"]).timestamp()*1000)
    bars = []
    for bar in history["rows"][:-1]:
        if bar["t"] + 300000 > received_ms:
            continue
        if bars and bar["t"] - bars[-1]["t"] != 300000:
            bars.clear()
        bars.append(bar)
    technical = metrics(bars)
    # Only descriptive values; no account, orders, execution price or inferred volume.
    evidence = {"kind": "market_and_technical", "symbol": report["symbol"],
                "metrics": report["metrics"], "technical": technical,
                "bar_interval": "5min", "last_closed_bar_ms": bars[-1]["t"], "closed_bars": report["contiguous_closed_bar_candidates"],
                "snapshot_availability": report["snapshot_availability"],
                "history_sha256": history["source_sha256"],
                "history_units_verified": False, "execution_price_eligible": False,
                "quote": {k: quote.get(k) for k in ("availability", "age_seconds", "values", "spread", "fresh_realtime_book_candidate")}}
    return ingest(provider="ibkr", text=json.dumps(evidence, sort_keys=True, allow_nan=False),
                  symbols=[report["symbol"]], received_at=as_of, ttl=300,
                  source_revision=_digest(evidence))


def institutional_once(*, subject, symbol, as_of):
    """Use integrity-checked original research claims; identity mapping is operator reviewed."""
    import os
    from datetime import timedelta
    from atlaspm.research.institutional_research import visible
    from atlaspm.research.financial_events import _time
    from atlaspm.data.provider_policy import local_rights
    local_rights("investment_banks", retention=True)
    if os.environ.get("ATLASPM_INVESTMENT_BANKS_LOCAL_AI_RIGHTS") != "1":
        raise ValueError("Institutional local AI rights required")
    cutoff = _time(as_of)
    added = 0
    for row in visible(as_of=as_of, subject=subject):
        if row["evidence_type"] != "original_report" or row["first_available_at"] is None:
            continue
        delivery = row["provider"]
        if os.environ.get("ATLASPM_" + delivery.upper() + "_RESEARCH_LOCAL_AI_RIGHTS") != "1":
            raise ValueError("Original report delivery local AI rights required")
        if cutoff - _time(row["published_at"]) > timedelta(days=30):
            continue
        evidence = {"kind": "institutional_claims", "subject": subject,
                    "firm": row["firm"], "published_at": row["published_at"],
                    "document_sha256": row["document_sha256"], "claims": row["claims"]}
        encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(encoded) > 24000:
            raise ValueError("Institutional claims exceed context budget")
        added += ingest(provider="investment_banks", text=encoded, symbols=[symbol],
                        received_at=row["received_at"], ttl=2592000,
                        source_revision=row["revision"])
    return added


def treasury_once(*, gateway=None):
    from atlaspm.data.api_gateway import DEFAULT_GATEWAY
    gateway = gateway or DEFAULT_GATEWAY
    url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
           "accounting/od/treasury_securities_auctions_v2?sort=-auction_date&page%5Bsize%5D=20")
    response = gateway.fetch("treasury", url)
    rows = response.data().get("data")
    if not isinstance(rows, list) or len(rows)>20:
        raise ValueError("Invalid auction page")
    from datetime import date
    added = 0
    for row in rows:
        if not isinstance(row,dict):
            raise ValueError("Invalid auction row")
        if date.fromisoformat(row["auction_date"]) > date.today():
            continue
        evidence = {k:row.get(k) for k in ("cusip","auction_date","security_type","security_term",
                    "high_yield","high_rate","bid_to_cover_ratio","total_accepted","issue_date")}
        added += ingest(provider="treasury", text=json.dumps(evidence,sort_keys=True),
                        symbols=[], received_at=response.fetched_at, ttl=86400,
                        source_revision=_digest(evidence))
    return added


def fred_once(series):
    import os
    from datetime import date, timedelta
    from atlaspm.data.fred_observations import observations
    # Series-level rights are not implied by access to FRED.
    if series not in os.environ.get("ATLASPM_FRED_LOCAL_AI_SERIES","").split(","):
        raise ValueError("FRED series local AI and retention rights required")
    today=date.today()
    report=observations(series,start=(today-timedelta(days=365)).isoformat(),
                        end=(today-timedelta(days=1)).isoformat(),as_of=today.isoformat())
    rows=[r for r in report["observations"] if r["value"]!="."][-12:]
    if not rows:
        return 0
    evidence={"series":series,"observations":rows,"publication_time_verified":False}
    return ingest(provider="fred",text=json.dumps(evidence,sort_keys=True),symbols=[],
                  received_at=report["retrieved_at_utc"],ttl=86400,
                  source_revision=_digest([series,[(r["date"],r["value"]) for r in rows]]))


def credit_once(*, scope, identifier_type, identifier, symbol):
    from datetime import date
    from atlaspm.research.credit_intelligence import fetch_ratings
    report=fetch_ratings(scope,identifier_type,[identifier],as_of=date.today().isoformat())
    return sum(ingest(provider="xpressapi",text=json.dumps(row,sort_keys=True),
                      symbols=[symbol],received_at=report["transport"]["retrieved_at_utc"],
                      ttl=86400,source_revision=_digest(row)) for row in report["ratings"])
