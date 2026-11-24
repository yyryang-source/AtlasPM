"""Collect and verify US equity close data before allowing a rebalance draft."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from calendar import monthrange
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from atlaspm.execution.ibkr_gateway import DEFAULT_GATEWAY_URL, LocalGatewayTransport, require_private_output
from atlaspm.rebalance_workbench import Policy, RunStore, exchange_window, latest_completed_session
from atlaspm.data.us_market_quality import audit_current_us_stock_prices
from atlaspm.data.us_market_universe import build_us_listed_universe, read_market_universe


PROVIDER = "Yahoo Finance via yfinance (Close and Adj Close)"
PRICE_HISTORY = 253
OVERLAP = 5
SCHEMA = "atlaspm_market_close_v1"
PRICE_FILES = {"universe": "universe.json", "adjusted": "adjusted.csv", "references": "close.csv"}
Quote = tuple[date, float, float]
QuoteFetcher = Callable[[str, date, date], list[Quote]]


def _json(path: Path, value: dict) -> None:
    partial = path.with_suffix(".part")
    partial.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    partial.replace(path)


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _yahoo_history(symbol: str, start: date, end: date) -> list[Quote]:
    import yfinance as yf

    frame = yf.Ticker(symbol).history(
        start=start.isoformat(), end=end.isoformat(), interval="1d", prepost=False,
        auto_adjust=False, actions=False, raise_errors=True, timeout=15,
    )
    return [
        (index.date(), float(adjusted), float(close))
        for index, adjusted, close in zip(frame.index, frame["Adj Close"], frame["Close"], strict=True)
    ]


def _requested_history(
    fetch: QuoteFetcher, symbol: str, start: date, day: date, sessions: set[date],
) -> dict[date, tuple[float, float]]:
    rows = {}
    for quote_day, adjusted, close in fetch(symbol, start, day + timedelta(days=1)):
        if not start <= quote_day <= day:
            raise ValueError("PROVIDER_RETURNED_WRONG_DATE")
        if quote_day not in sessions or quote_day in rows:
            raise ValueError("PROVIDER_RETURNED_NON_SESSION_OR_DUPLICATE")
        if not all(math.isfinite(value) and value > 0 for value in (adjusted, close)):
            raise ValueError("PROVIDER_RETURNED_INVALID_CLOSE")
        rows[quote_day] = adjusted, close
    return rows


def _refresh_symbol(
    db: sqlite3.Connection, symbol: str, sessions: tuple[date, ...], fetch: QuoteFetcher,
    *, reference_only: bool = False,
) -> str:
    day = sessions[-1]
    need = {day} if reference_only else set(sessions[-PRICE_HISTORY:])
    existing = {
        date.fromisoformat(stamp): (adjusted, close)
        for stamp, adjusted, close in db.execute(
            "SELECT session, adjusted, close FROM quotes WHERE symbol = ? AND session BETWEEN ? AND ?",
            (symbol, sessions[0].isoformat(), day.isoformat()),
        )
    }
    if need <= existing.keys():
        return "cached"
    bootstrap = not reference_only and (
        not existing or not set(sessions[-PRICE_HISTORY:-OVERLAP]) <= existing.keys()
    )
    start = day if reference_only else sessions[0] if bootstrap else sessions[-OVERLAP - 1]
    valid_days = set(sessions)
    incoming = _requested_history(fetch, symbol, start, day, valid_days)
    changed = any(
        not all(math.isclose(a, b, rel_tol=2e-6, abs_tol=1e-6)
                for a, b in zip(previous, incoming[stamp], strict=True))
        for stamp, previous in existing.items() if stamp in incoming
    )
    # Corporate actions can change the vendor's adjustment of the entire history.
    if changed and not bootstrap and not reference_only:
        bootstrap = True
        start = sessions[0]
        incoming = _requested_history(fetch, symbol, start, day, valid_days)
    combined = {stamp: value for stamp, value in existing.items() if stamp < start} | incoming
    if day not in incoming:
        raise ValueError("MISSING_DECISION_CLOSE")
    if not need <= combined.keys():
        raise ValueError(f"MISSING_REQUIRED_HISTORY: {len(need - combined.keys())} session(s)")
    with db:
        db.execute("DELETE FROM quotes WHERE symbol = ? AND session BETWEEN ? AND ?",
                   (symbol, start.isoformat(), day.isoformat()))
        db.executemany("INSERT INTO quotes VALUES (?, ?, ?, ?)", (
            (symbol, stamp.isoformat(), adjusted, close)
            for stamp, (adjusted, close) in sorted(incoming.items())
        ))
    return "reloaded" if changed else "initial" if bootstrap else "extended"


def _write_quotes(
    db: sqlite3.Connection, destination: Path, catalog: list[dict], sessions: tuple[date, ...],
) -> None:
    first, last = sessions[0].isoformat(), sessions[-1].isoformat()
    with (destination / PRICE_FILES["adjusted"]).open("w", newline="", encoding="utf-8") as adjusted, (
        destination / PRICE_FILES["references"]
    ).open("w", newline="", encoding="utf-8") as references:
        ap, rp = csv.writer(adjusted), csv.writer(references)
        ap.writerow(("date", "symbol", "adjusted_close"))
        rp.writerow(("date", "symbol", "close"))
        for row in catalog:
            symbol = row["provider_symbol"]
            for stamp, value, close in db.execute(
                """SELECT session, adjusted, close FROM quotes
                   WHERE symbol = ? AND session BETWEEN ? AND ? ORDER BY session""",
                (symbol, first, last),
            ):
                ap.writerow((stamp, symbol, format(value, ".12g")))
                if stamp == last:
                    rp.writerow((stamp, symbol, format(close, ".12g")))


def _scoped_manifest(path: Path, catalog: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(catalog[0]))
        writer.writeheader()
        writer.writerows(catalog)


def _month_end(day: date) -> bool:
    import pandas_market_calendars as calendars

    last = date(day.year, day.month, monthrange(day.year, day.month)[1])
    sessions = calendars.get_calendar("NYSE").valid_days(day, last)
    return bool(len(sessions) and sessions[-1].date() == day)


def prepare_close(
    directory: Path, manifest: Path, *, day: date, now: datetime, symbols: tuple[str, ...] = (),
    schedule: str = "daily", delay_minutes: int = 20, fetch: QuoteFetcher = _yahoo_history,
    source: str = PROVIDER, max_symbols: int = 0, additional_quotes: tuple[str, ...] = (),
) -> Path:
    """Create an immutable dated audit attempt, resuming all previously cached successes."""
    sessions, close = exchange_window(day)
    if not 0 <= delay_minutes <= 240 or now.tzinfo is None or now < close + timedelta(minutes=delay_minutes):
        raise ValueError("Decision session has not cleared the post-close delay")
    if schedule not in {"daily", "month-end"} or max_symbols < 0:
        raise ValueError("Invalid close schedule or batch limit")
    manifest_digest = _digest(manifest)
    members = read_market_universe(manifest)
    if manifest_digest != _digest(manifest):
        raise ValueError("Listing manifest changed during preparation")
    by_symbol = {row["provider_symbol"].strip().upper(): row for row in members}
    requested = set(symbols)
    extra = set(additional_quotes) - requested if requested else set(additional_quotes) - by_symbol.keys()
    if (len(requested) != len(symbols) or requested - by_symbol.keys()
            or extra - by_symbol.keys()):
        raise ValueError("Strategy and additional quote symbols must be unique current listing-manifest entries")
    if any(not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", symbol) for symbol in requested | extra):
        raise ValueError("Invalid strategy or additional quote symbol")
    catalog = [by_symbol[symbol] | {"provider_symbol": symbol} for symbol in sorted(requested or by_symbol)]
    extras = [by_symbol[symbol] | {"provider_symbol": symbol} for symbol in sorted(extra)]
    directory.mkdir(parents=True, exist_ok=True)
    identity = json.dumps([day.isoformat(), sorted(requested), sorted(extra), schedule, delay_minutes, source])
    key = hashlib.sha256(identity.encode()).hexdigest()[:16]
    # Hold the writer lock separately so validated price updates can commit per symbol.
    with closing(sqlite3.connect(directory / "writer.sqlite3", timeout=0.0)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        return _run_close(directory, manifest_digest, day, now, sessions, catalog, extras, requested,
                          schedule, source, key, fetch, max_symbols)


def _run_close(
    directory: Path, manifest_digest: str, day: date, now: datetime, sessions: tuple[date, ...],
    catalog: list[dict], extras: list[dict], requested: set[str], schedule: str, source: str,
    key: str, fetch: QuoteFetcher, max_symbols: int,
) -> Path:
    close_day = directory / day.isoformat() / key
    pointer = close_day / "latest.json"
    if pointer.is_file():
        saved_id = json.loads(pointer.read_text(encoding="utf-8"))["attempt"]
        if not re.fullmatch(r"[a-f0-9]{32}", saved_id):
            raise ValueError("Invalid close attempt identifier")
        previous = close_day / saved_id / "audit.json"
        last = json.loads(previous.read_text(encoding="utf-8"))
        if last["status"] == "ready" and last["manifest_sha256"] == manifest_digest:
            return previous
    attempt = close_day / uuid4().hex
    attempt.mkdir(parents=True)
    failures = {}
    updates: dict[str, int] = {}
    attempted = 0
    rate_limited = False
    with closing(sqlite3.connect(directory / "quotes.sqlite3")) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS quotes (
                   symbol TEXT NOT NULL, session TEXT NOT NULL, adjusted REAL NOT NULL, close REAL NOT NULL,
                   PRIMARY KEY (symbol, session))""")
        db.execute("CREATE TABLE IF NOT EXISTS quote_source (name TEXT PRIMARY KEY)")
        db.execute("""CREATE TABLE IF NOT EXISTS attempts (
                   symbol TEXT NOT NULL, session TEXT NOT NULL, calls INTEGER NOT NULL,
                   PRIMARY KEY (symbol, session))""")
        saved_sources = {row[0] for row in db.execute("SELECT name FROM quote_source")}
        if saved_sources and saved_sources != {source}:
            raise ValueError("Use a separate close directory for each quote provider")
        with db:
            db.execute("INSERT OR IGNORE INTO quote_source VALUES (?)", (source,))

        counts = dict(db.execute("SELECT symbol, calls FROM attempts WHERE session = ?", (day.isoformat(),)))

        def counted(symbol: str, start: date, end: date) -> list[Quote]:
            nonlocal attempted
            attempted += 1
            with db:
                db.execute(
                    """INSERT INTO attempts VALUES (?, ?, 1)
                       ON CONFLICT(symbol, session) DO UPDATE SET calls = calls + 1""",
                    (symbol, day.isoformat()),
                )
            return fetch(symbol, start, end)

        extra_symbols = {row["provider_symbol"] for row in extras}
        for row in sorted(catalog + extras, key=lambda row: counts.get(row["provider_symbol"], 0)):
            symbol = row["provider_symbol"]
            if rate_limited or (max_symbols and attempted >= max_symbols):
                break
            try:
                outcome = _refresh_symbol(db, symbol, sessions, counted, reference_only=symbol in extra_symbols)
            except Exception as exc:
                failures[symbol] = f"{type(exc).__name__}: {exc}"
                if re.search(r"ratelimit|rate.limit|too many requests|\b429\b", failures[symbol], re.I):
                    rate_limited = True
                continue
            updates[outcome] = updates.get(outcome, 0) + 1
        _write_quotes(db, attempt, catalog, sessions)
        # Held instruments outside the selection universe also need current marks.
        references = attempt / PRICE_FILES["references"]
        with references.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            for row in extras:
                symbol = row["provider_symbol"]
                quote = db.execute("SELECT close FROM quotes WHERE symbol=? AND session=?",
                                   (symbol, day.isoformat())).fetchone()
                if quote:
                    writer.writerow((day.isoformat(), symbol, format(quote[0], ".12g")))
                else:
                    failures.setdefault(symbol, "MISSING_REQUIRED_ACCOUNT_REFERENCE")
    manifest_copy = attempt / "listings.csv"
    _scoped_manifest(manifest_copy, catalog)
    size = len(catalog)
    audit = audit_current_us_stock_prices(
        universe_path=manifest_copy, prices_path=attempt / PRICE_FILES["adjusted"], as_of=day.isoformat(),
        min_recent_coverage_pct=100.0 if requested else 85.0,
        min_month_end_coverage_pct=100.0 if requested else 90.0,
        min_eligible_coverage_pct=100.0 if requested else 75.0,
        min_eligible_names=size if requested else 500,
    )
    audit["data_scope"] = "configured_strategy_symbols" if requested else audit["data_scope"]
    feed_symbols = [row["provider_symbol"] for row in catalog]
    reasons = list(audit["review_reasons"])
    if requested and any(symbol in failures for symbol in requested):
        reasons.append("STRATEGY_SYMBOL_REFRESH_FAILED")
    if any(symbol in failures for symbol in (row["provider_symbol"] for row in extras)):
        reasons.append("ACCOUNT_REFERENCE_MISSING")
    eligible = set(audit["eligible_symbols"])
    if not requested and len(eligible) != size:
        reasons.append("FULL_MANIFEST_REQUIRES_EXPLICIT_STRATEGY_SCOPE")
    _json(attempt / PRICE_FILES["universe"], {
        "as_of": day.isoformat(), "symbols": feed_symbols,
        "source": f"{source} / {'configured strategy universe' if requested else 'current listing manifest'}",
    })
    report = {
        "schema": SCHEMA, "as_of": day.isoformat(), "observed_at": now.isoformat(), "provider": source,
        "status": "blocked" if reasons else "ready", "reasons": reasons, "market_audit": audit,
        "rebalance_due": schedule == "daily" or _month_end(day), "schedule": schedule,
        "manifest_sha256": manifest_digest, "download_calls": attempted, "rate_limited": rate_limited,
        "updates": updates, "quote_errors": failures,
        "input_sha256": {name: _digest(attempt / filename) for name, filename in PRICE_FILES.items()},
    }
    _json(attempt / "audit.json", report)
    _json(pointer, {"attempt": attempt.name})
    if reasons:
        # Incomplete attempts retain the diagnostics. The resumable quote
        # cache already holds the validated observations for subsequent runs.
        for filename in PRICE_FILES.values():
            (attempt / filename).unlink()
        manifest_copy.unlink()
    return attempt / "audit.json"


def approved_inputs(audit_path: Path) -> tuple[date, bytes, bytes, bytes, str]:
    """Load exactly the reviewed files from a date-bound successful close attempt."""
    audit_bytes = audit_path.read_bytes()
    report = json.loads(audit_bytes)
    if report["schema"] != SCHEMA or report["status"] != "ready" or report["reasons"]:
        raise ValueError("Market-close audit blocked downstream rebalancing")
    if report["rebalance_due"] is not True:
        raise ValueError("Strategy has no scheduled rebalance for this session")
    day = date.fromisoformat(report["as_of"])
    if (report["market_audit"]["as_of_nyse_session"] != day.isoformat()
            or report["market_audit"]["market_data_status"] != "READY_FOR_RESEARCH_REVIEW"):
        raise ValueError("Market-close audit is inconsistent or incomplete")
    data: dict[str, bytes] = {}
    for name, filename in PRICE_FILES.items():
        data[name] = (audit_path.parent / filename).read_bytes()
        if hashlib.sha256(data[name]).hexdigest() != report["input_sha256"][name]:
            raise ValueError(f"Market-close file differs from its audited version: {filename}")
    return day, data["universe"], data["adjusted"], data["references"], hashlib.sha256(audit_bytes).hexdigest()


def _listing_manifest(args: argparse.Namespace, directory: Path, day: date) -> Path:
    if args.manifest is not None:
        return args.manifest
    manifest = directory / f"listings-{day}.csv"
    if not manifest.is_file():
        temporary = directory / f".listings-{uuid4().hex}.part"
        try:
            build_us_listed_universe(temporary)
            temporary.replace(manifest)
        finally:
            temporary.unlink(missing_ok=True)
    return manifest


def _collect_latest(args: argparse.Namespace, directory: Path, now: datetime) -> Path:
    day = latest_completed_session(now - timedelta(minutes=args.delay_minutes))
    manifest = _listing_manifest(args, directory, day)
    return prepare_close(
        directory, manifest, day=day, now=now, symbols=tuple(args.symbols or ()),
        schedule=args.schedule, delay_minutes=args.delay_minutes, max_symbols=args.max_symbols,
        additional_quotes=tuple(args.additional_quotes),
    )


def _draft_if_configured(args: argparse.Namespace, directory: Path, audit: Path) -> dict:
    if not args.draft_account:
        return {}
    from atlaspm.close_drafts import draft_after_close

    def account_marks(day: date, additional: tuple[str, ...]) -> Path:
        return prepare_close(
            directory, _listing_manifest(args, directory, day), day=day, now=datetime.now(timezone.utc),
            symbols=tuple(args.symbols), schedule=args.schedule, delay_minutes=args.delay_minutes,
            max_symbols=args.max_symbols, additional_quotes=tuple(sorted(set(args.additional_quotes) | set(additional))),
        )

    transport = LocalGatewayTransport(args.gateway_url, ca_file=args.gateway_ca_file)
    return draft_after_close(directory, audit, RunStore(Path("local_data/rebalance/workbench.sqlite3")),
                             transport, account_id=args.draft_account, policy=Policy(allocation=args.allocation),
                             prepare_account_marks=account_marks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("local_data/market_close"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--symbols", nargs="+", help="Explicit cash-equity strategy scope")
    parser.add_argument("--additional-quotes", nargs="+", default=[])
    parser.add_argument("--schedule", choices=("daily", "month-end"), default="daily")
    parser.add_argument("--delay-minutes", type=int, default=20)
    parser.add_argument("--max-symbols", type=int, default=100, help="Cap download calls per invocation, 0 for all")
    parser.add_argument("--watch", action="store_true", help="Poll exchange-calendar boundaries and retry incomplete sessions")
    parser.add_argument("--poll-seconds", type=int, default=900)
    parser.add_argument("--draft-account", help="Optional dedicated USD cash account for GET-only order drafts")
    parser.add_argument("--dedicated-cash-equities", action="store_true")
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--gateway-ca-file")
    parser.add_argument("--allocation", choices=("equal", "quant"), default="equal")
    args = parser.parse_args()
    if not 0 <= args.delay_minutes <= 240 or args.max_symbols < 0 or not 60 <= args.poll_seconds <= 3600:
        parser.error("invalid close delay, download limit or poll interval")
    if args.draft_account and (not args.dedicated_cash_equities or not args.symbols):
        parser.error("--draft-account requires an explicit --symbols scope and --dedicated-cash-equities")
    directory = require_private_output(args.directory / "placeholder").parent
    directory.mkdir(parents=True, exist_ok=True)
    last_event: str | None = None
    while True:
        try:
            now = datetime.now(timezone.utc)
            path = _collect_latest(args, directory, now)
            report = json.loads(path.read_text(encoding="utf-8"))
            draft = {}
            try:
                draft = _draft_if_configured(args, directory, path)
            except (OSError, ValueError, sqlite3.Error) as exc:
                draft = {"status": "blocked", "error": str(exc)}
            event = json.dumps({"audit": str(path), "as_of": report["as_of"], "status": report["status"],
                                "download_calls": report["download_calls"], "reasons": report["reasons"],
                                **({"draft": draft} if draft else {})})
            if event != last_event:
                print(event, flush=True)
                if args.watch:
                    with (directory / "watch.log").open("a", encoding="utf-8") as log:
                        log.write(f"{now.isoformat()} {event}\n")
                last_event = event
            if not args.watch:
                return 0 if report["status"] == "ready" and draft.get("status") not in {
                    "blocked", "account_marks_blocked", "draft_blocked",
                } else 2
        except (OSError, ValueError, sqlite3.Error) as exc:
            event = f"Market close preparation failed: {exc}"
            print(event, file=sys.stderr, flush=True)
            if not args.watch:
                return 2
            try:
                with (directory / "watch.log").open("a", encoding="utf-8") as log:
                    log.write(f"{datetime.now(timezone.utc).isoformat()} {event}\n")
            except OSError:
                pass
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
