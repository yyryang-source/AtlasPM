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


def annualized_sample_covariance(rows, means):
    """Sample covariance of aligned daily returns, computed once per pair."""
    n = len(rows)
    size = len(means)
    if n < 2 or size < 1:
        raise ValueError("Sample covariance requires two observations and one asset")
    covariance = [[0.0] * size for _ in range(size)]
    # Cache just one centered column at a time, rather than allocating an
    # additional full return matrix. Keep the original scalar path for
    # other numeric types, custom sequences, and malformed input.
    cache_columns = (
        4 <= size and n <= 10000
        and type(rows) is list and type(means) is list
        and all(type(mean) is float for mean in means)
        and all(type(row) is list and len(row) == size
                and all(type(value) is float for value in row) for row in rows)
    )
    for i in range(size):
        centered = [row[i] - means[i] for row in rows] if cache_columns else None
        for j in range(i, size):
            if centered is None:
                products = ((row[i] - means[i]) * (row[j] - means[j]) for row in rows)
            else:
                products = (left * (row[j] - means[j]) for left, row in zip(centered, rows))
            value = TRADING_DAYS_PER_YEAR * sum(products) / (n - 1)
            covariance[i][j] = covariance[j][i] = value
    return covariance


def rolling_return_estimates(rows, symbols, *, shrinkage=0., half_life_sessions=None,
                             diagonal_floor=0.):
    """Annualized past-window arithmetic estimates with optional decay.

    Rows must be chronological, aligned, one-session simple returns. This
    kernel cannot certify the upstream calendar, identity or adjustment
    conventions. Diagonal shrinkage preserves the estimated marginal
    variances; the optional floor is an explicit additive regularizer.
    No mandatory scientific dependency and no second covariance warehouse.
    """
    from math import exp, log
    from atlaspm.engine.contracts import finite
    n,p=len(rows),len(symbols)
    if (not 3<=n<=10000 or not 1<=p<=256 or len(set(symbols))!=p
            or any(not isinstance(s,str) or not s for s in symbols)):
        raise ValueError("INVALID_ROLLING_RISK_ESTIMATION_SCOPE")
    shrink=finite(shrinkage,"covariance_shrinkage",minimum=0)
    floor=finite(diagonal_floor,"covariance_diagonal_floor",minimum=0)
    if shrink>1 or floor>1:
        raise ValueError("INVALID_COVARIANCE_REGULARIZATION")
    for row in rows:
        if len(row)!=p or any(not isfinite(v) or v<=-1 for v in row):
            raise ValueError("INVALID_ALIGNED_RETURN_ROW")
    if half_life_sessions is None:
        means=[fmean(row[i] for row in rows) for i in range(p)]
        cov=annualized_sample_covariance(rows,means)
        effective=float(n)
    else:
        half=finite(half_life_sessions,"covariance_half_life_sessions",minimum=1)
        if half>10000:
            raise ValueError("INVALID_COVARIANCE_HALF_LIFE")
        raw=[exp(-log(2)*(n-1-i)/half) for i in range(n)]
        total=sum(raw)
        weights=[v/total for v in raw]
        concentration=sum(w*w for w in weights)
        effective=1/concentration
        if effective<3:
            raise ValueError("INSUFFICIENT_EFFECTIVE_COVARIANCE_OBSERVATIONS")
        means=[sum(w*row[i] for w,row in zip(weights,rows)) for i in range(p)]
        scale=TRADING_DAYS_PER_YEAR/(1-concentration)
        cov=[[0.]*p for _ in range(p)]
        for i in range(p):
            for j in range(i,p):
                value=scale*sum(w*(row[i]-means[i])*(row[j]-means[j])
                                for w,row in zip(weights,rows))
                cov[i][j]=cov[j][i]=value
    for i in range(p):
        cov[i][i]+=floor
        for j in range(i):
            cov[i][j]*=1-shrink
            cov[j][i]=cov[i][j]
    if any(not isfinite(v) for row in cov for v in row):
        raise ValueError("NONFINITE_ROLLING_COVARIANCE")
    expected={s:means[i]*TRADING_DAYS_PER_YEAR for i,s in enumerate(symbols)}
    if any(not isfinite(v) for v in expected.values()):
        raise ValueError("NONFINITE_ROLLING_MEAN")
    return expected,cov,{"method":"sample" if half_life_sessions is None else "exponentially_weighted",
                         "return_observations":n,"effective_observations":effective,
                         "half_life_sessions":half_life_sessions,"diagonal_shrinkage":shrink,
                         "diagonal_floor":floor,"annualization_sessions":TRADING_DAYS_PER_YEAR,
                         "historical_mean_is_forecast":False,
                         "calendar_and_source_independently_verified":False}


def estimate_from_prices(path: Path, min_observations: int = 60) -> dict[str, Any]:
    """Compute aligned daily simple returns and sample annualized covariance.

    Calendar intersection avoids missing-price forward-filling. We report
    sample arithmetic annualized return = mean daily return * 252, not CAGR.
    """
    if isinstance(min_observations, bool) or not isinstance(min_observations, int) or min_observations < 3:
        raise ValueError("min_observations must be an integer >= 3")
    prices = load_adjusted_prices(path)
    symbols = sorted(prices)
    shared = set(prices[symbols[0]])
    for symbol in symbols[1:]:
        shared.intersection_update(prices[symbol])
    dates = sorted(shared)
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
    cov = annualized_sample_covariance(daily, means)
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
