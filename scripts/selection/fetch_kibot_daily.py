"""Fetch a fixed set of Kibot guest daily histories for offline research evaluation."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date, datetime
from http.cookiejar import CookieJar
from io import StringIO
import json
from math import isfinite
from pathlib import Path
import re
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, build_opener

from atlaspm.ibkr_gateway import require_private_output


_API = "https://api.kibot.com/?"
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,19}")


def _daily_rows(raw: bytes, start: date, end: date) -> list[tuple[date, float]]:
    bars = []
    records = csv.reader(StringIO(raw.decode("utf-8-sig")))
    for row in records:
        if not row:
            continue
        if len(row) != 6:
            raise ValueError("response is not a daily OHLCV series")
        day = datetime.strptime(row[0], "%m/%d/%Y").date()
        close = float(row[4])
        if not isfinite(close) or close <= 0 or (bars and day <= bars[-1][0]):
            raise ValueError("daily close is invalid or dates are out of order")
        if start <= day <= end:
            bars.append((day, close))
    if not bars:
        raise ValueError("no bars within the requested window")
    return bars


def fetch_daily(source: Path, start: date, end: date, cache: Path, output: Path, workers: int, *, unadjusted: bool) -> dict:
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"symbol", "provider_symbol"}.issubset(reader.fieldnames or ()):
            raise ValueError("Symbol map needs symbol,provider_symbol")
        symbols = [(r["symbol"].strip().upper(), r["provider_symbol"].strip().upper()) for r in reader]
    if (not symbols or len({symbol for symbol, _ in symbols}) != len(symbols) or start > end
            or not 1 <= workers <= 5 or not all(_SYMBOL.fullmatch(x) for pair in symbols for x in pair)):
        raise ValueError("Use a unique symbol map, ordered date range and one to five workers")
    if output.exists():
        raise FileExistsError(output)
    cache.mkdir(parents=True, exist_ok=True)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def request(**params: str) -> bytes:
        with opener.open(_API + urlencode(params), timeout=20) as result:
            return result.read()

    def cache_path(provider: str) -> Path:
        suffix = "unadjusted" if unadjusted else "adjusted"
        return cache / f"{provider}_{start}_{end}_{suffix}.csv"

    if any(not cache_path(provider).is_file() for _, provider in symbols):
        session = request(action="login", user="guest", password="guest")
        status_line = session.splitlines()[0] if session else b""
        preexisting_guest = status_line.startswith(b"407 Already logged in ") and status_line.endswith(b" as 'guest'")
        if not status_line.startswith(b"200 OK") and not preexisting_guest:
            raise ValueError("Kibot guest authorization did not succeed")

    halt = Event()

    def history(provider: str) -> list[tuple[date, float]] | str:
        if halt.is_set():
            return "session stopped"
        cache_file = cache_path(provider)
        try:
            cached = cache_file.exists()
            raw = (cache_file.read_bytes() if cached else request(
                action="history", symbol=provider, interval="daily", startdate=start.strftime("%m/%d/%Y"),
                enddate=end.strftime("%m/%d/%Y"), **({"unadjusted": "1"} if unadjusted else {}),
            ))
            if raw[:3] in {b"401", b"403", b"429"}:
                halt.set()
                raise RuntimeError("Kibot guest session was rejected or throttled")
            bars = _daily_rows(raw, start, end)
            if not cached:
                with cache_file.open("xb") as handle:
                    handle.write(raw)
            return bars
        except HTTPError as exc:
            if exc.code in {401, 403, 429}:
                halt.set()
                raise RuntimeError(f"Kibot rejected or throttled data requests ({exc.code})") from exc
            return f"http_{exc.code}"
        except (URLError, TimeoutError) as exc:
            return type(exc).__name__
        except ValueError as exc:
            return str(exc)

    successful = {}
    missing = {}
    providers = sorted({provider for _, provider in symbols})
    provider_results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, (provider, result) in enumerate(zip(providers, pool.map(history, providers), strict=True), 1):
            provider_results[provider] = result
            if index % 50 == 0 or index == len(providers):
                print(f"Kibot daily histories {index}/{len(providers)}", flush=True)
    for symbol, provider in symbols:
        result = provider_results[provider]
        if isinstance(result, str):
            missing[symbol] = {"provider_symbol": provider, "reason": result}
        else:
            successful[symbol] = result
    if not successful:
        raise ValueError("No daily histories were available for the supplied symbol map")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "symbol", "close" if unadjusted else "adjusted_close"])
        for symbol, bars in sorted(successful.items()):
            writer.writerows((day, symbol, close) for day, close in bars)
    return {"requested_symbols": len(symbols), "downloaded_symbols": len(successful), "missing": missing,
            "unadjusted": unadjusted,
            "symbols": {symbol: {"first_day": str(bars[0][0]), "last_day": str(bars[-1][0]), "rows": len(bars)}
                        for symbol, bars in sorted(successful.items())}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol-map", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--unadjusted", action="store_true")
    args = parser.parse_args()
    output = require_private_output(args.output)
    report = require_private_output(args.report)
    if report.exists() or report == output:
        raise ValueError("Use separate new output and report paths")
    result = fetch_daily(args.symbol_map, args.start, args.end, require_private_output(args.cache), output, args.workers,
                         unadjusted=args.unadjusted)
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps({name: result[name] for name in ("requested_symbols", "downloaded_symbols", "missing")}))
