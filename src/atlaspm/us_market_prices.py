"""Resume-safe bulk daily adjusted-close retrieval for a CURRENT US stock universe.

Cached one-symbol CSVs and final assembled data live in private local_data.
Market data is unofficial Yahoo Finance; failed tickers never silently pass as
covered. This is NOT a historical delisted universe or trading/execution API.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import date
from math import isfinite
import json
import os
import sys
from pathlib import Path
import time
from typing import Any, Callable

from atlaspm.us_market_universe import read_market_universe


def _market_history(symbol: str, start: str, end: str) -> list[tuple[str, float]]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ValueError("Install Yahoo market data adapter: pip install -e '.[market]'") from exc
    frame = yf.Ticker(symbol).history(
        start=start, end=end, interval="1d", auto_adjust=True,
        actions=False, raise_errors=True,
    )
    if frame is None or frame.empty or "Close" not in frame.columns:
        raise ValueError("NO_HISTORY_RETURNED")
    begin = date.fromisoformat(start)
    finish = date.fromisoformat(end)
    values = []
    seen: set[str] = set()
    for index, raw in frame["Close"].items():
        day = index.date().isoformat()
        price = float(raw)
        if day in seen or not (begin <= date.fromisoformat(day) < finish):
            continue
        if not isfinite(price) or price <= 0:
            continue
        values.append((day, price))
        seen.add(day)
    if not values:
        raise ValueError("NO_VALID_DAILY_CLOSE")
    return sorted(values)


def _paths(cache_dir: Path, ticker: str) -> tuple[Path, Path]:
    if not ticker or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for c in ticker):
        raise ValueError(f"Unsafe quote symbol: {ticker}")
    return cache_dir / (ticker + ".csv"), cache_dir / (ticker + ".json")


def _cached(cache_dir: Path, symbol: str, start: str, end: str) -> int:
    prices, meta = _paths(cache_dir, symbol)
    if not prices.exists() or not meta.exists():
        return 0
    try:
        record = json.loads(meta.read_text(encoding="utf-8"))
        if (record.get("symbol") != symbol or record.get("start") != start
                or record.get("end") != end):
            return 0
        count = int(record["rows"])
        if count <= 0 or prices.stat().st_size < 40:
            return 0
        return count
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _store(cache_dir: Path, symbol: str, start: str, end: str,
           rows: list[tuple[str, float]]) -> int:
    prices, meta = _paths(cache_dir, symbol)
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Parent is constrained to local_data by the CLI, ticker filename sanitized.
    tmp_csv = prices.with_suffix(".csv.part")
    tmp_meta = meta.with_suffix(".json.part")
    with tmp_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "symbol", "adjusted_close"])
        writer.writerows((d, symbol, format(p, ".12g")) for d, p in rows)
    tmp_meta.write_text(json.dumps({
        "symbol": symbol, "start": start, "end": end, "rows": len(rows),
        "first_date": rows[0][0], "last_date": rows[-1][0],
        "source": "Yahoo Finance via yfinance; unofficial; auto_adjust=True",
    }, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_csv, prices)
    os.replace(tmp_meta, meta)
    return len(rows)


def sync_us_market_prices(
    universe_file: Path,
    cache_dir: Path,
    output: Path,
    *,
    start: str,
    end: str,
    max_symbols: int = 0,
    workers: int = 2,
    attempts: int = 2,
    downloader: Callable[[str, str, str], list[tuple[str, float]]] | None = None,
) -> dict[str, Any]:
    """Download quote histories incrementally, then assemble all cached symbols.

    max_symbols=0 covers entire current listing; N>0 is smoke-testing or
    incremental progress, never a claim of full market coverage.
    Existing per-ticker cache is used only for the exact same date interval.
    """
    try:
        begin = date.fromisoformat(start)
        finish = date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("start/end must be YYYY-MM-DD") from exc
    if finish <= begin:
        raise ValueError("end (exclusive) must be after start")
    if isinstance(max_symbols, bool) or not 0 <= max_symbols <= 100_000:
        raise ValueError("max-symbols must be in 0..100000")
    if isinstance(workers, bool) or not 1 <= workers <= 8:
        raise ValueError("workers must be in 1..8")
    if isinstance(attempts, bool) or not 1 <= attempts <= 3:
        raise ValueError("attempts must be in 1..3")
    universe = read_market_universe(universe_file)
    symbols = [r["provider_symbol"] for r in universe]
    if output.resolve() == universe_file.resolve():
        raise ValueError("Cannot overwrite universe manifest with prices")
    if output.resolve() == cache_dir.resolve():
        raise ValueError("Output must differ from cache directory")
    fetch = downloader or _market_history

    uncached = [s for s in symbols if not _cached(cache_dir, s, start, end)]
    selected = uncached[:max_symbols] if max_symbols else uncached
    failures: dict[str, str] = {}
    succeeded = 0

    def worker(symbol: str) -> tuple[str, int, str | None]:
        last_error = "UNKNOWN_ERROR"
        for attempt in range(attempts):
            try:
                rows = fetch(symbol, start, end)
                if not rows:
                    raise ValueError("NO_VALID_CLOSE_ROWS")
                return symbol, _store(cache_dir, symbol, start, end, rows), None
            except Exception as exc:  # Vendor/API errors must not abort thousands of symbols.
                last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
                if attempt + 1 < attempts:
                    time.sleep(0.75 * (attempt + 1))
        return symbol, 0, last_error

    if selected:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker, symbol) for symbol in selected]
            for done, future in enumerate(as_completed(futures), 1):
                symbol, rows, error = future.result()
                if error:
                    failures[symbol] = error
                else:
                    succeeded += 1
                if done % 25 == 0 or done == len(selected):
                    print(
                        f"[stock-sync-market] {done}/{len(selected)} requested, "
                        f"{succeeded} successful, {len(failures)} failed; "
                        "successful quotes will be cached",
                        file=sys.stderr, flush=True,
                    )

    # Assembly is streaming and may generate a large file for a 26-year
    # full exchange history; no 30-million-row in-memory dataframe.
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".part")
    covered = 0
    rows_total = 0
    missing = []
    with temp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "symbol", "adjusted_close"])
        for symbol in symbols:
            count = _cached(cache_dir, symbol, start, end)
            if not count:
                missing.append(symbol)
                continue
            path, _ = _paths(cache_dir, symbol)
            try:
                with path.open("r", encoding="utf-8", newline="") as src:
                    reader = csv.DictReader(src)
                    if not reader.fieldnames or not {"date", "symbol", "adjusted_close"}.issubset(reader.fieldnames):
                        raise ValueError(f"Corrupt cache header for {symbol}")
                    num = 0
                    for row in reader:
                        if row["symbol"] != symbol:
                            raise ValueError(f"Corrupt cache symbol for {symbol}")
                        writer.writerow((row["date"], symbol, row["adjusted_close"]))
                        num += 1
                if num != count:
                    raise ValueError(f"Cache length mismatch for {symbol}")
            except Exception:
                temp.unlink(missing_ok=True)
                raise
            rows_total += num
            covered += 1
    os.replace(temp, output)
    return {
        "source": "Yahoo Finance via yfinance (unofficial)",
        "period": {"start_inclusive": start, "end_exclusive": end},
        "listing_candidates": len(symbols),
        "cached_or_downloaded_symbols": covered,
        "missing_symbols": len(missing),
        "coverage_pct": round(100.0 * covered / len(symbols), 2),
        "price_rows": rows_total,
        "new_download_successes": succeeded,
        "attempted_downloads": len(selected),
        "failed_this_run": len(failures),
        "failures": failures,
        "missing_tickers": missing,
        "remaining_or_failed": len(missing),
        "full_market_coverage": len(missing) == 0,
        "output": str(output),
        "cache_dir": str(cache_dir),
        "live_execution_enabled": False,
        "warnings": [
            "Only current US-listed universe; delisted and historical-only securities absent.",
            "Some listings have no reliable Yahoo history; completeness is shown, not assumed.",
            "Adjusted data may contain survivorship and corporate-action revision biases.",
            "Price data alone do not supply verified point-in-time company fundamentals.",
            "All market data are unofficial research inputs; no trading actions.",
        ],
    }
