"""Evidence-bound Unusual Whales options research, not a directional order signal.

Flow alerts are aggregates which can overlap; never sum alert premiums as tape
volume or infer beneficial owner, opening position, dealer inventory or hedge.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
import re
from urllib.parse import urlencode

from atlaspm.data.api_gateway import DEFAULT_GATEWAY, GatewayError
from atlaspm.data.provider_policy import local_rights

BASE = "https://api.unusualwhales.com"
_ENDPOINTS = {
    "greek_exposure": "/api/stock/{ticker}/greek-exposure",
    "net_premium": "/api/stock/{ticker}/net-prem-ticks",
    "option_contracts": "/api/stock/{ticker}/option-contracts",
    "iv_rank": "/api/stock/{ticker}/iv-rank",
    "iv_term_structure": "/api/stock/{ticker}/volatility/term-structure",
    "dark_pool": "/api/darkpool/{ticker}",
}
_FLAGS = {"research_only": True, "approved_for_model_training": False,
          "approved_for_backtest": False, "live_execution_enabled": False,
          "broker_order_submission_enabled": False, "external_ai_allowed": False,
          "point_in_time_verified": False}


def _ticker(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9.]{1,12}", value):
        raise ValueError("Expected uppercase provider ticker")
    return value


def _stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Expected timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("Missing timestamp offset")
    return stamp.astimezone(UTC)


def _decimal(value, *, optional=False, signed=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)) or len(str(value)) > 64:
        raise ValueError("Invalid numeric option field")
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Invalid option number") from None
    if not d.is_finite() or abs(d) > Decimal("1e18") or (not signed and d < 0):
        raise ValueError("Invalid option number")
    return d


def _text(value):
    return None if value is None else str(value)


def _bool(row, name):
    value = row.get(name)
    if value is not None and type(value) is not bool:
        raise ValueError("Invalid option flag")
    return value


def analyze_alerts(response, *, as_of, max_age_seconds=300, min_premium=100000):
    """Validate one bounded page and produce independent per-alert diagnostics.

    All times are provider-reported and retrieval-bound, not certified PIT.
    Historical use is exploratory even when created_at predates the cutoff.
    """
    cutoff, received = _stamp(as_of), _stamp(response.fetched_at)
    if response.provider != "unusual_whales" or cutoff > datetime.now(UTC):
        raise ValueError("Invalid flow source or cutoff")
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400:
        raise ValueError("Invalid freshness threshold")
    threshold = _decimal(min_premium)
    rows = response.data().get("data")
    if not isinstance(rows, list) or len(rows) > 250:
        raise GatewayError("Expected at most 250 flow alerts")
    seen, result, excluded = {}, [], Counter()
    clusters = Counter()
    for row in rows:
        if not isinstance(row, dict):
            raise GatewayError("Invalid flow row")
        eid = row.get("id")
        if not isinstance(eid, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", eid):
            raise GatewayError("Invalid flow ID")
        canonical = json.dumps(row, sort_keys=True, allow_nan=False)
        if eid in seen:
            if canonical != seen[eid]:
                raise GatewayError("Conflicting duplicate flow ID")
            excluded["duplicate_id"] += 1
            continue
        seen[eid] = canonical
        created, start, end = (_stamp(row[k]) for k in ("created_at", "start_time", "end_time"))
        if not start <= end <= created <= received:
            raise GatewayError("Invalid flow chronology")
        if created > cutoff:
            excluded["after_cutoff"] += 1
            continue
        ticker, kind = _ticker(row["ticker"]), str(row["type"]).lower()
        chain = row.get("option_chain")
        if kind not in {"call", "put"} or not isinstance(chain, str) or not re.fullmatch(r"[A-Z0-9. :_-]{1,64}", chain):
            raise GatewayError("Invalid contract description")
        premium = _decimal(row["total_premium"])
        if premium < threshold:
            excluded["below_threshold"] += 1
            continue
        ask = _decimal(row.get("total_ask_side_prem"), optional=True)
        bid = _decimal(row.get("total_bid_side_prem"), optional=True)
        if any(x is not None and x > premium for x in (ask, bid)) or (
                ask is not None and bid is not None and ask + bid > premium):
            raise GatewayError("Inconsistent alert premium")
        vol = _decimal(row.get("volume"), optional=True)
        oi = _decimal(row.get("open_interest"), optional=True)
        ratio = vol / oi if vol is not None and oi is not None and oi > 0 else None
        iv_start = _decimal(row.get("iv_start"), optional=True)
        iv_end = _decimal(row.get("iv_end"), optional=True)
        multi, single, sweep, floor, opening = (_bool(row, k) for k in
            ("has_multileg", "has_singleleg", "has_sweep", "has_floor", "all_opening_trades"))
        age = (cutoff-created).total_seconds()
        fresh = received <= cutoff and age <= max_age_seconds
        # Side imbalance is descriptive only, not a net opening or directional position.
        imbalance = ask - bid if ask is not None and bid is not None else None
        reasons = ["HEDGE_OR_COUNTERPARTY_INTENT_UNKNOWN", "AGGREGATED_ALERT_MAY_OVERLAP"]
        if multi is not False or single is not True:
            reasons.append("MULTILEG_OR_UNKNOWN_LEG_STRUCTURE")
        if opening is not True:
            reasons.append("OPENING_STATUS_UNCERTAIN")
        if not fresh:
            reasons.append("STALE_OR_RETRIEVED_AFTER_CUTOFF")
        clusters[(ticker, kind, chain, created.replace(second=0, microsecond=0).isoformat())] += 1
        result.append({"alert_id": eid, "ticker": ticker, "option_chain": chain, "type": kind,
                       "created_at": created.isoformat(), "start_time": start.isoformat(),
                       "end_time": end.isoformat(), "age_seconds": age, "fresh": fresh,
                       "premium": str(premium), "ask_premium": _text(ask), "bid_premium": _text(bid),
                       "alert_ask_minus_bid_premium": _text(imbalance), "volume": _text(vol),
                       "open_interest": _text(oi), "volume_oi_ratio": _text(ratio),
                       "volume_above_oi": ratio > 1 if ratio is not None else None,
                       "iv_change": _text(iv_end-iv_start if iv_start is not None and iv_end is not None else None),
                       "has_sweep": sweep, "has_floor": floor, "has_multileg": multi,
                       "has_singleleg": single, "all_opening_trades": opening,
                       "directional_signal": None, "risk_notes": reasons})
    result.sort(key=lambda row: (row["created_at"], row["alert_id"]))
    return {**_FLAGS, "provider": response.provider, "model": "options_alert_rules_v1",
            "as_of": cutoff.isoformat(), "transport": response.evidence(), "alerts": result,
            "counts": dict(Counter(r["type"] for r in result)), "excluded": dict(excluded),
            "clusters": [{"ticker": k[0], "type": k[1], "option_chain": k[2], "minute": k[3], "alerts": n}
                         for k, n in sorted(clusters.items()) if n > 1],
            "coverage_complete": False, "premium_aggregated_across_alerts": False,
            "block_trade_classification_verified": False, "calibrated_probability": None}


def fetch_alerts(*, ticker=None, limit=100, min_premium=100000, older_than=None,
                 gateway=DEFAULT_GATEWAY):
    local_rights("unusual_whales")
    if type(limit) is not int or not 1 <= limit <= 250:
        raise ValueError("Flow limit must be 1..250")
    query = {"limit": limit, "min_premium": str(_decimal(min_premium))}
    if ticker is not None:
        query["ticker_symbol"] = _ticker(ticker)
    if older_than is not None:
        query["older_than"] = _stamp(older_than).isoformat()
    response = gateway.fetch("unusual_whales", BASE + "/api/option-trades/flow-alerts?" + urlencode(query))
    report = analyze_alerts(response, as_of=response.fetched_at, min_premium=min_premium)
    if older_than is not None and any(_stamp(row["created_at"]) >= _stamp(older_than) for row in report["alerts"]):
        raise GatewayError("Flow page does not honor requested historical cursor")
    return report


def fetch_context(kind, ticker, *, gateway=DEFAULT_GATEWAY):
    """Fetch a separate documented context dataset without guessing its units.

    Context pages are unnormalized provider data. In particular GEX/DEX sign,
    time basis, participant category and units require schema reconciliation.
    """
    local_rights("unusual_whales")
    if kind not in _ENDPOINTS:
        raise ValueError("Unknown options context")
    response = gateway.fetch("unusual_whales", BASE + _ENDPOINTS[kind].format(ticker=_ticker(ticker)))
    data = response.data().get("data")
    if not isinstance(data, list) or len(data) > 10000 or any(not isinstance(row, dict) for row in data):
        raise GatewayError("Invalid or oversized context page")
    exposures = []
    if kind == "greek_exposure":
        for row in data:
            # Retain categories separately; dealer-side and units unverified.
            vals = {k:_text(_decimal(row.get(k),optional=True,signed=True))
                    for k in ("call_gamma","put_gamma","call_delta","put_delta")}
            if not any(v is not None for v in vals.values()):
                raise GatewayError("Greek exposure response missing documented fields")
            exposures.append({"date":row.get("date"),"member_type":row.get("member_type"),
                              "issuer":row.get("issuer"),**vals})
    return {**_FLAGS, "kind": kind, "ticker": ticker, "transport": response.evidence(),
            "model": "provider_context_unreconciled", "rows": data, "exposure_fields": exposures,
            "units_and_publication_time_verified": False, "coverage_complete": False}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--ticker")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--older-than")
    parser.add_argument("--context", choices=tuple(_ENDPOINTS))
    args = parser.parse_args()
    report = fetch_context(args.context, args.ticker) if args.context else fetch_alerts(
        ticker=args.ticker, limit=args.limit, older_than=args.older_than)
    # Console only displays counts/transport; restricted research payload not exported.
    report.pop("alerts", None)
    report.pop("clusters", None)
    report.pop("exposure_fields", None)
    rows = report.pop("rows", None)
    if rows is not None:
        report["row_count"] = len(rows)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
