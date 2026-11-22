"""Deterministic offline stock screener; research rankings, never trade intents.

Price observations and explicitly dated, user-supplied fundamentals are cut off
at the requested evaluation date. Retrospectively adjusted vendor prices and
unverified fundamental publication dates are not a point-in-time data guarantee.
"""

from __future__ import annotations

import csv
from datetime import date
from math import isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from atlaspm.historical import load_adjusted_prices

_FUND_COLUMNS = (
    "as_of_date", "symbol", "sector", "market_cap_usd", "roe",
    "revenue_growth_yoy", "debt_to_equity", "fcf_yield", "ev_to_ebitda",
)
_TECH_WEIGHTS = {
    "momentum_12_1": .30, "momentum_6_1": .20,
    "price_vs_sma200": .15, "annual_volatility": .20,
    "max_drawdown_252": .15,
}
_MIXED_WEIGHTS = {
    "momentum_12_1": .15, "momentum_6_1": .10,
    "price_vs_sma200": .10, "annual_volatility": .10,
    "max_drawdown_252": .10, "roe": .10,
    "revenue_growth_yoy": .10, "debt_to_equity": .08,
    "fcf_yield": .10, "ev_to_ebitda": .07,
}
_LOWER_BETTER = {"annual_volatility", "debt_to_equity", "ev_to_ebitda"}


def _fundamental_rows(path: Path, cutoff: date) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, date]] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not set(_FUND_COLUMNS).issubset(reader.fieldnames):
            raise ValueError("Fundamentals CSV needs: " + ",".join(_FUND_COLUMNS))
        for number, record in enumerate(reader, 2):
            try:
                symbol = record["symbol"].strip().upper()
                observed = date.fromisoformat(record["as_of_date"].strip())
                sector = record["sector"].strip()
            except (ValueError, AttributeError, TypeError) as exc:
                raise ValueError(f"Invalid fundamentals date/symbol/sector row {number}") from exc
            if not symbol or not sector:
                raise ValueError(f"Missing symbol or sector at fundamentals row {number}")
            key = (symbol, observed)
            if key in seen:
                raise ValueError(f"Duplicate fundamentals snapshot for {symbol} on {observed}")
            seen.add(key)
            if observed > cutoff:
                continue  # Future-publication snapshots must never influence historical ranks.
            try:
                vals = {name: float(record[name]) for name in _FUND_COLUMNS[3:]}
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Invalid fundamentals numeric data at row {number}") from exc
            if any(not isfinite(x) for x in vals.values()):
                raise ValueError(f"Non-finite fundamentals at row {number}")
            if (vals["market_cap_usd"] <= 0 or vals["debt_to_equity"] < 0
                    or vals["ev_to_ebitda"] <= 0):
                raise ValueError(f"Invalid market cap, debt/equity or EV/EBITDA at row {number}")
            # Ratios must be decimals, e.g. 0.20 means 20%, not 20.
            if (not -5 <= vals["roe"] <= 5
                    or not -1 <= vals["revenue_growth_yoy"] <= 10
                    or not -5 <= vals["fcf_yield"] <= 5):
                raise ValueError(f"Check decimal ratio units at fundamentals row {number}")
            existing = latest.get(symbol)
            if existing is None or observed > existing["as_of_date"]:
                latest[symbol] = {"as_of_date": observed, "sector": sector, **vals}
    return latest


def _drawdown(prices: list[float]) -> float:
    high = prices[0]
    worst = 0.0
    for price in prices[1:]:
        high = max(high, price)
        worst = min(worst, price / high - 1.0)
    return worst


def _rank_factors(rows: list[dict[str, Any]], weights: dict[str, float]) -> None:
    size = len(rows)
    for factor in weights:
        ordered = sorted(rows, key=lambda row: (row["factors"][factor], row["symbol"]))
        i = 0
        while i < size:
            j = i + 1
            while j < size and ordered[j]["factors"][factor] == ordered[i]["factors"][factor]:
                j += 1
            percentile = 100.0 * ((i + j) / 2.0) / size
            if factor in _LOWER_BETTER:
                percentile = 100.0 - percentile
            for k in range(i, j):
                ordered[k]["factor_percentiles"][factor] = round(percentile, 4)
            i = j
    for row in rows:
        row["score"] = round(sum(
            weight * row["factor_percentiles"][name]
            for name, weight in weights.items()
        ), 4)


def screen_stocks(
    prices_path: Path,
    as_of: str,
    *,
    fundamentals_path: Path | None = None,
    top: int = 10,
    max_price_age_days: int = 10,
    max_fundamental_age_days: int = 200,
    max_annual_volatility: float = .80,
    max_drawdown: float = .65,
    minimum_market_cap_usd: float = 0.0,
    require_uptrend: bool = False,
    max_per_sector: int | None = None,
) -> dict[str, Any]:
    """Rank historical snapshots, with pre-scoring data/risk eligibility checks.

    No prices on later dates enter a score. Fundamentals as_of_date represents
    the *first publicly available* date, not a fiscal period-end date.
    """
    """Screen a CSV price source through an explicit date cutoff."""
    return screen_stocks_from_prices(
        load_adjusted_prices(prices_path), as_of,
        fundamentals_path=fundamentals_path, top=top,
        max_price_age_days=max_price_age_days,
        max_fundamental_age_days=max_fundamental_age_days,
        max_annual_volatility=max_annual_volatility,
        max_drawdown=max_drawdown,
        minimum_market_cap_usd=minimum_market_cap_usd,
        require_uptrend=require_uptrend,
        max_per_sector=max_per_sector,
    )


def screen_stocks_from_prices(
    prices: dict[str, dict[date, float]],
    as_of: str,
    *,
    fundamentals_path: Path | None = None,
    top: int = 10,
    max_price_age_days: int = 10,
    max_fundamental_age_days: int = 200,
    max_annual_volatility: float = .80,
    max_drawdown: float = .65,
    minimum_market_cap_usd: float = 0.0,
    require_uptrend: bool = False,
    max_per_sector: int | None = None,
    require_decision_close: bool = False,
) -> dict[str, Any]:
    """Rank historical snapshots, with pre-scoring data/risk eligibility checks.

    No prices on later dates enter a score. Fundamentals as_of_date represents
    the *first publicly available* date, not a fiscal period-end date.
    """
    try:
        cutoff = date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError("--as-of must be YYYY-MM-DD") from exc
    if (top < 1 or max_price_age_days < 1 or max_fundamental_age_days < 1
            or (max_per_sector is not None and max_per_sector < 1)):
        raise ValueError("top, freshness limits and sector cap must be positive integers")
    if not (isfinite(max_annual_volatility) and max_annual_volatility > 0
            and isfinite(max_drawdown) and 0 < max_drawdown <= 1
            and isfinite(minimum_market_cap_usd) and minimum_market_cap_usd >= 0):
        raise ValueError("Invalid volatility, drawdown, or minimum market-cap limit")
    if fundamentals_path is None and (minimum_market_cap_usd > 0 or max_per_sector is not None):
        raise ValueError("Market-cap and sector caps require --fundamentals")

    fundamentals = _fundamental_rows(fundamentals_path, cutoff) if fundamentals_path else {}
    weights = _MIXED_WEIGHTS if fundamentals_path else _TECH_WEIGHTS
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for symbol, time_series in sorted(prices.items()):
        observations = sorted((day, price) for day, price in time_series.items() if day <= cutoff)
        reason = ""
        if len(observations) < 253:
            reason = "INSUFFICIENT_HISTORY_253_SESSIONS"
        elif require_decision_close and observations[-1][0] != cutoff:
            reason = "MISSING_DECISION_CLOSE"
        elif (cutoff - observations[-1][0]).days > max_price_age_days:
            reason = "STALE_PRICE"
        elif (observations[-1][0] - observations[-253][0]).days > 420:
            reason = "SPARSE_PRICE_HISTORY"
        elif any((b[0] - a[0]).days > 14 for a, b in zip(observations[-253:-1], observations[-252:])):
            reason = "PRICE_GAP_GT_14_DAYS"
        if reason:
            excluded.append({"symbol": symbol, "reason": reason})
            continue

        trailing = [price for _, price in observations[-253:]]
        daily = [b / a - 1.0 for a, b in zip(trailing[-64:-1], trailing[-63:])]
        vol = stdev(daily) * sqrt(252)
        dd = _drawdown(trailing)
        sma200 = fmean(trailing[-200:])
        above_sma = trailing[-1] >= sma200
        if vol > max_annual_volatility:
            reason = "VOLATILITY_LIMIT"
        elif -dd > max_drawdown:
            reason = "DRAWDOWN_LIMIT"
        elif require_uptrend and not above_sma:
            reason = "BELOW_SMA200"
        source = fundamentals.get(symbol)
        if not reason and fundamentals_path:
            if source is None:
                reason = "FUNDAMENTALS_NOT_AVAILABLE_AS_OF_DATE"
            elif (cutoff - source["as_of_date"]).days > max_fundamental_age_days:
                reason = "STALE_FUNDAMENTALS"
            elif source["market_cap_usd"] < minimum_market_cap_usd:
                reason = "MARKET_CAP_LIMIT"
        if reason:
            excluded.append({"symbol": symbol, "reason": reason})
            continue
        factors = {
            "momentum_12_1": trailing[-22] / trailing[-253] - 1.0,
            "momentum_6_1": trailing[-22] / trailing[-127] - 1.0,
            "price_vs_sma200": trailing[-1] / sma200 - 1.0,
            "annual_volatility": vol,
            "max_drawdown_252": dd,
        }
        row: dict[str, Any] = {
            "symbol": symbol,
            "last_price_date": observations[-1][0].isoformat(),
            "sector": source["sector"] if source else None,
            "fundamentals_as_of": source["as_of_date"].isoformat() if source else None,
            "above_sma200": above_sma,
            "factors": factors,
            "factor_percentiles": {},
        }
        if source:
            row["market_cap_usd"] = source["market_cap_usd"]
            factors.update({name: source[name] for name in _FUND_COLUMNS[4:]})
        eligible.append(row)
    if eligible:
        _rank_factors(eligible, weights)
    ranked = sorted(eligible, key=lambda row: (-row["score"], row["symbol"]))
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
    picks: list[dict[str, Any]] = []
    sector_counts: dict[str, int] = {}
    for row in ranked:
        sector = row["sector"]
        if max_per_sector is not None and sector_counts.get(sector, 0) >= max_per_sector:
            continue
        picks.append(row)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(picks) >= top:
            break
    return {
        "method": "offline_cross_sectional_factor_percentile_v1",
        "as_of": cutoff.isoformat(),
        "mode": "fundamental_plus_technical" if fundamentals_path else "technical_only",
        "weights": weights,
        "filters": {
            "max_price_age_days": max_price_age_days,
            "max_fundamental_age_days": max_fundamental_age_days if fundamentals_path else None,
            "max_annual_volatility": max_annual_volatility,
            "max_drawdown": max_drawdown,
            "minimum_market_cap_usd": minimum_market_cap_usd,
            "require_uptrend": require_uptrend,
            "max_per_sector": max_per_sector,
        },
        "universe_count": len(prices),
        "eligible_count": len(ranked),
        "excluded": excluded,
        "ranked": ranked,
        "selected": picks,
        "live_execution_enabled": False,
        "warnings": [
            "Ranking and relative factor percentiles are NOT forecasts or trade approvals.",
            "Fundamentals dates are user-asserted PUBLICATION/availability dates; fiscal quarter ends are not valid substitutes.",
            "Retrospectively adjusted closes and today's surviving ticker universe can cause historical lookahead/survivorship bias.",
            "No liquidity, spread, corporate-action, sector-neutral, delisting or independent data-provenance audit.",
            "No realized-return out-of-sample evaluation, transaction-cost simulation or broker connectivity.",
        ],
    }
