"""FRED/ALFRED date-vintage observations; ephemeral exploratory response.

No new macro warehouse. A requested vintage date is not verified intraday
publication time, and series-specific licensing must be reviewed.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlencode

from atlaspm.data.api_gateway import DEFAULT_GATEWAY, GatewayError


def observations(series, *, start, end, as_of, gateway=DEFAULT_GATEWAY):
    if not isinstance(series, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", series):
        raise ValueError("Invalid FRED series identifier")
    first, last, decision = (date.fromisoformat(value) for value in (start, end, as_of))
    if not first <= last < decision <= date.today() or (last-first).days > 366:
        raise ValueError("Require at most 367 days, strictly before decision date")
    query = urlencode({"series_id": series, "observation_start": first.isoformat(),
                       "observation_end": last.isoformat(), "realtime_start": decision.isoformat(),
                       "realtime_end": decision.isoformat(), "file_type": "json",
                       "limit": 1000, "sort_order": "asc"})
    response = gateway.fetch("fred", "https://api.stlouisfed.org/fred/series/observations?" + query)
    data = response.data()
    rows = data.get("observations")
    if not isinstance(rows, list) or type(data.get("count")) is not int or data["count"] != len(rows) or len(rows) > 1000:
        raise GatewayError("FRED observation coverage or page mismatch")
    result = []
    previous = None
    for row in rows:
        try:
            observed = date.fromisoformat(row["date"])
            vintage_first = date.fromisoformat(row["realtime_start"])
            vintage_last = date.fromisoformat(row["realtime_end"])
            value = row["value"]
            if (not first <= observed <= last or (previous is not None and observed <= previous)
                    or not vintage_first <= decision <= vintage_last or not isinstance(value, str)
                    or len(value) > 128 or (value != "." and not Decimal(value).is_finite())):
                raise ValueError("Invalid observation")
        except (ValueError, TypeError, KeyError, InvalidOperation):
            raise GatewayError("Invalid FRED observation or vintage") from None
        previous = observed
        result.append({"series": series, "date": observed.isoformat(), "value": value,
                       "realtime_start": vintage_first.isoformat(), "realtime_end": vintage_last.isoformat()})
    return {**response.evidence(), "series": series, "as_of": decision.isoformat(),
            "observations": result, "historical_publication_time_verified": False}
