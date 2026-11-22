"""Clean, aligned adjusted-close history -> annualized research estimates.

No prices are forward-filled: assets must have prices on the same dates.
These are backwards-looking sample statistics, not expected future returns.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from math import isfinite
from pathlib import Path
from statistics import fmean
from typing import Any

TRADING_DAYS_PER_YEAR = 252


def load_adjusted_prices(path: Path) -> dict[str, dict[date, float]]:
    """Load long-form CSV: date,symbol,adjusted_close (strictly positive)."""
    prices: dict[str, dict[date, float]] = defaultdict(dict)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"date", "symbol", "adjusted_close"}.issubset(reader.fieldnames):
            raise ValueError("CSV needs columns: date,symbol,adjusted_close")
        for line_no, record in enumerate(reader, start=2):
            try:
                observed_date = date.fromisoformat(record["date"].strip())
                symbol = record["symbol"].strip().upper()
                close = float(record["adjusted_close"])
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid price CSV row {line_no}") from exc
            if not symbol or not isfinite(close) or close <= 0:
                raise ValueError(f"Invalid symbol or adjusted_close in row {line_no}")
            if observed_date in prices[symbol]:
                raise ValueError(f"Duplicate price for {symbol} on {observed_date}")
            prices[symbol][observed_date] = close
    if not prices:
        raise ValueError("No valid price rows in CSV")
    return dict(prices)


def estimate_from_prices(path: Path, min_observations: int = 60) -> dict[str, Any]:
    """Compute aligned daily simple returns and sample annualized covariance.

    Calendar intersection avoids missing-price forward-filling. We report
    sample arithmetic annualized return = mean daily return * 252, not CAGR.
    """
    if isinstance(min_observations, bool) or not isinstance(min_observations, int) or min_observations < 3:
        raise ValueError("min_observations must be an integer >= 3")
    prices = load_adjusted_prices(path)
    symbols = sorted(prices)
    dates = sorted(set.intersection(*(set(prices[s]) for s in symbols)))
    if len(dates) < min_observations + 1:
        raise ValueError(
            f"Need at least {min_observations + 1} shared close dates; got {len(dates)}"
        )
    daily: list[list[float]] = []
    for previous, current in zip(dates, dates[1:]):
        daily.append([prices[s][current] / prices[s][previous] - 1 for s in symbols])
    observations = len(daily)
    means = [fmean(row[i] for row in daily) for i in range(len(symbols))]
    if any(not isfinite(mu) for mu in means):
        raise ValueError("Non-finite sample return")
    cov = [
        [
            TRADING_DAYS_PER_YEAR * sum(
                (row[i] - means[i]) * (row[j] - means[j])
                for row in daily
            ) / (observations - 1)
            for j in range(len(symbols))
        ]
        for i in range(len(symbols))
    ]
    if any(not isfinite(cov[i][i]) or cov[i][i] <= 0 for i in range(len(symbols))):
        raise ValueError("One or more symbols have zero or invalid return volatility")
    expected_returns = {
        symbol: means[i] * TRADING_DAYS_PER_YEAR
        for i, symbol in enumerate(symbols)
    }
    return {
        "expected_returns": expected_returns,
        "covariance": cov,
        "metadata": {
            "source_file": str(path),
            "statistic": "historical_sample_arithmetic_mean_and_sample_covariance",
            "adjusted_price_field": "adjusted_close",
            "method": "daily_simple_returns_common_dates_no_forward_fill",
            "annualization_trading_days": TRADING_DAYS_PER_YEAR,
            "return_observations": observations,
            "shared_price_dates": len(dates),
            "first_price_date": dates[0].isoformat(),
            "last_price_date": dates[-1].isoformat(),
            "symbols": symbols,
            "warnings": [
                "Historical sample average is NOT a forecast of future returns.",
                "Adjusted-close conventions depend on provider and can be revised.",
                "Common-date sampling drops dates missing for any symbol.",
                "No out-of-sample test or corporate-action audit was performed.",
            ],
        },
    }


def download_yahoo_adjusted_prices(
    symbols: list[str], start: str, end: str, path: Path, overwrite: bool = False
) -> dict[str, Any]:
    """Fetch daily auto-adjusted Close via yfinance, store in local CSV.

    yfinance is optional and imported only for this explicit network action.
    Yahoo Finance is a research-oriented, unofficial public data source.
    start inclusive, end exclusive as defined by yfinance.
    """
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ValueError(
            "Install optional market-data dependency: python -m pip install -e '.[market]'"
        ) from exc
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("start/end must be ISO dates YYYY-MM-DD") from exc
    if last <= first:
        raise ValueError("end (exclusive) must be later than start")
    if path.exists() and not overwrite:
        raise ValueError(f"Refusing to overwrite {path}; add --overwrite if intentional")
    canonical = [s.strip().upper() for s in symbols]
    if not canonical or any(not s or not s.replace('-', '').replace('.', '').isalnum() for s in canonical):
        raise ValueError("symbols must be valid ticker-like strings")
    if len(set(canonical)) != len(canonical):
        raise ValueError("Duplicate symbols are not allowed")

    observations: list[tuple[str, str, float]] = []
    counts: dict[str, int] = {}
    for ticker in canonical:
        try:
            frame = yf.Ticker(ticker).history(
                start=start, end=end, interval="1d",
                auto_adjust=True, actions=False, raise_errors=True
            )
        except Exception as exc:
            raise ValueError(f"Price retrieval failed for {ticker}: {exc}") from exc
        if frame is None or frame.empty or "Close" not in frame.columns:
            raise ValueError(f"No adjusted close history for {ticker}")
        count = 0
        for index, raw_close in frame["Close"].items():
            try:
                close = float(raw_close)
            except (TypeError, ValueError):
                continue
            if not isfinite(close) or close <= 0:
                continue
            day = index.date().isoformat()
            if first <= date.fromisoformat(day) < last:
                observations.append((day, ticker, close))
                count += 1
        if count == 0:
            raise ValueError(f"No usable price rows for {ticker}")
        counts[ticker] = count
    # No partial file is written unless every requested ticker succeeded.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "symbol", "adjusted_close"])
        for day, symbol, price in sorted(observations):
            writer.writerow([day, symbol, format(price, ".12g")])
    return {
        "provider": "Yahoo Finance via yfinance (unofficial)",
        "field": "Close with auto_adjust=True",
        "start_inclusive": start,
        "end_exclusive": end,
        "output_path": str(path),
        "rows_per_symbol": counts,
        "note": "Research use only; recheck source terms, adjustments and data quality.",
    }
