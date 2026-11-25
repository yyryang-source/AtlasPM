"""Read-only, bounded access to existing market stores.

Observed dates and provider symbols are not certified publication timestamps
or permanent security IDs. No training, backtest certification or execution
permissions are issued by this exploratory interface.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
import csv
from decimal import Decimal
from math import isfinite
from pathlib import Path
import sqlite3
import re
from types import MappingProxyType
from zoneinfo import ZoneInfo

from atlaspm.data import factor_db, hf_stock5m, hf_stock5m_batch
from atlaspm.data import hf_stock5m_filings_v52 as filings
from atlaspm.data import hf_stock5m_security_master_v53 as security_master
from atlaspm.data import hf_stock5m_session_v4 as sessions
from atlaspm.data.market_db import DB_PATH, private_path
from atlaspm.data.hf_stock5m_certification_v5 import fingerprint
from atlaspm.data.stock_fundamentals_import import _PROVENANCE_COLUMNS
from atlaspm.strategies.equity.stock_screener import FUNDAMENTAL_COLUMNS, fundamental_value_issue


class DataUnavailable(ValueError):
    """The requested source, partition, or coverage is unavailable."""


class DataQualityError(ValueError):
    """Selected rows violate a structural invariant."""


class AdmissionDenied(PermissionError):
    """No certified point-in-time data is supplied by these adapters."""


@dataclass(frozen=True, slots=True)
class Dataset:
    store: str
    relation: str
    keys: tuple[str, ...]
    fields: tuple[str, ...]
    timeframe: str = ""
    source: str = ""


def _option(timeframe):
    return Dataset("sqlite", "option_bars", ("symbol", "date_et", "timestamp_ms"),
                   ("open", "high", "low", "close", "volume", "vwap", "trade_count"),
                   timeframe)


CATALOG = MappingProxyType({
    "equity_daily": Dataset("sqlite", "equity_daily", ("symbol", "trade_date"),
                            ("adjusted_close",)),
    "equity_1m": Dataset("parquet", "hf_raw", ("symbol", "trade_date_et", "timestamp_ms"),
                         ("open", "high", "low", "close", "volume_shares"), source="hf"),
    "equity_5m": Dataset("parquet", "hf", ("symbol", "trade_date_et", "timestamp_ms"),
                         ("open", "high", "low", "close", "volume_shares",
                          "source_minute_bars", "unique_source_minutes"), source="hf"),
    "option_1m": _option("1m"), "option_5m": _option("5m"), "option_daily": _option("1d"),
    "option_quotes": Dataset("sqlite", "option_quotes", ("symbol", "timestamp_ms"),
                             ("bid", "ask", "bid_size", "ask_size")),
    "option_greeks": Dataset("sqlite", "option_greeks", ("symbol", "observed_at_ms"),
                             ("underlying_price", "iv", "delta", "gamma", "theta", "vega", "rho")),
    "treasury_auctions": Dataset("sqlite", "treasury_auctions",
                                 ("cusip", "auction_date", "security_type"),
                                 ("security_term", "coupon_rate_pct", "high_yield_pct", "offering_amount")),
    "bond_quotes": Dataset("sqlite", "bond_quotes", ("cusip", "quote_date"),
                           ("bid_price", "ask_price", "yield_to_maturity_pct")),
    "sec_filings": Dataset("sec_index", "filings", ("cik", "filed_date", "form", "source_url", "quarter"),
                           ("issuer", "index_sha256", "parser_version")),
    **{f"factors_{source}": Dataset("parquet", "factors", ("symbol", "trade_date"),
                                  ("close", "volume_shares", "observed_5m_bars",
                                   "observed_1m_bars", "last_observed_bar_ms") + factor_db.FEATURES,
                                  source=source) for source in factor_db.SOURCES},
})

# Explicitly unavailable through this API, not fabricated empty tables.
UNCONNECTED = frozenset({
    "equity_realtime",
    "macro", "option_contracts", "bond_terms",
})


@dataclass(frozen=True, slots=True)
class Batch:
    dataset: str
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    origin: str
    source_version_hint: tuple[int, int]
    point_in_time_verified: bool = False
    approved_for_model_training: bool = False
    approved_for_backtest: bool = False
    live_execution_enabled: bool = False


def _day(value):
    if not isinstance(value, str):
        raise ValueError("Dates must be ISO YYYY-MM-DD strings")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Dates must be ISO YYYY-MM-DD strings")
    return parsed


def _partitions(spec, start, end):
    # Do not use HF's availability-clipping month planner: gaps must fail.
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        month = f"{y:04d}-{m:02d}"
        if spec.relation == "hf_raw":
            path = hf_stock5m.raw_path(month)
        elif spec.relation == "hf":
            path = hf_stock5m.output_path(month, "rth")
        else:
            path = factor_db._path(month, spec.source, "rth", "factors")
        if not path.is_file():
            raise DataUnavailable(f"Missing {spec.relation} partition: {month}")
        yield private_path(path, exists=True)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _version(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _sql(spec, columns, symbols, start, end, relation):
    # All identifiers come from the fixed catalog; all user values are bound.
    field = spec.keys[1]
    if field.endswith("_ms"):
        # Quote/Greek dates are UTC, not exchange-local trading days.
        epoch = date(1970, 1, 1)
        where = f"{field} >= ? AND {field} < ?"
        params = [(start - epoch).days * 86400000,
                  ((end - epoch).days + 1) * 86400000]
    else:
        where = f"{field} >= ? AND {field} <= ?"
        params = [start.isoformat(), end.isoformat()]
    if symbols:
        where += f" AND {spec.keys[0]} IN (" + ",".join("?" for _ in symbols) + ")"
        params.extend(symbols)
    if spec.timeframe:
        where += " AND timeframe = ?"
        params.append(spec.timeframe)
    return (f"SELECT {','.join(columns)} FROM {relation} WHERE {where}"
            f" ORDER BY {','.join(spec.keys)}"), params


def _raw_minute_relation(con, path):
    """Reuse existing raw QC; do not silently drop invalid or ambiguous rows."""
    try:
        _, invalid = hf_stock5m._check_raw(con, path)
    except ValueError as exc:
        raise DataQualityError(str(exc)) from exc
    if invalid:
        raise DataQualityError("Raw partition has invalid OHLCV; use existing quarantine workflow")
    relation = factor_db._files_sql([path])
    if con.execute(f"""SELECT 1 FROM {relation}
        WHERE epoch_us(timestamp) % 60000000 <> 0 LIMIT 1""").fetchone():
        raise DataQualityError("Raw minute timestamps are not aligned")
    # Nominal exchange-local RTH, not a verified session/holiday calendar.
    # Preserve source values; no adjustment or invented missing observations.
    return f"""(SELECT upper(trim(ticker)) AS symbol,
        CAST(timezone('America/New_York', timestamp) AS DATE) AS trade_date_et,
        epoch_ms(timestamp) AS timestamp_ms, open, high, low, close,
        volume AS volume_shares FROM {relation}
        WHERE CAST(timezone('America/New_York', timestamp) AS TIME) >= TIME '09:30:00'
          AND CAST(timezone('America/New_York', timestamp) AS TIME) < TIME '16:00:00')"""


def _decision_ms(value):
    if not isinstance(value, str):
        raise ValueError("Decision time must be ISO datetime with UTC offset")
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("Decision time must include UTC offset")
    return stamp, int(stamp.timestamp() * 1000)


def _session_relation(con, spec, path, relation, cutoff):
    """Exchange-calendar candidate bars, not actual vendor publication times."""
    month = path.stem.removeprefix("ohlcv_")
    if spec.relation == "hf":
        view = sessions.install_research_view(con, month)
        # The V4 field denotes nominal bucket end, not independent availability.
        return f"(SELECT *, bar_available_at_ms AS bar_end_ms FROM {view} WHERE bar_available_at_ms <= {cutoff})"
    sessions.install_calendar(con, sessions.calendar_rows(month))
    return f"""(SELECT a.*, a.timestamp_ms + 60000 AS bar_end_ms
        FROM {relation} a JOIN v4_nyse s ON a.trade_date_et=s.trade_date
        WHERE a.timestamp_ms >= s.open_ms
          AND a.timestamp_ms + 60000 <= s.close_ms
          AND a.timestamp_ms + 60000 <= {cutoff})"""


def _finite_numeric(value):
    """Accept native SQLite and DuckDB numerics without coercing Decimal."""
    if type(value) is int:
        return True
    if type(value) is float:
        return isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    return False


_POSITIVE_FIELDS = frozenset({"open", "high", "low", "close", "adjusted_close"})
_NONNEGATIVE_FIELDS = frozenset({"volume", "volume_shares", "bid", "ask", "bid_size", "ask_size"})


def _validate_field(field, value):
    if isinstance(value, (float, Decimal)) and not _finite_numeric(value):
        raise DataQualityError(f"Non-finite value in {field}")
    if field in _POSITIVE_FIELDS:
        if not _finite_numeric(value) or value <= 0:
            raise DataQualityError(f"Invalid price in {field}")
    elif field in _NONNEGATIVE_FIELDS:
        if value is not None and (not _finite_numeric(value) or value < 0):
            raise DataQualityError(f"Invalid nonnegative field: {field}")
    elif field == "source" and (not isinstance(value, str) or not value.strip()):
        raise DataQualityError("Missing source")


def _batches(cursor, *, name, spec, columns, path, batch_size, version):
    previous = None
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            return
        for row in rows:
            key = row[:len(spec.keys)]
            if any(v is None or v == "" for v in key) or (previous is not None and key <= previous):
                raise DataQualityError("Missing or duplicate/unordered observation key")
            previous = key
            for field, value in zip(columns, row):
                _validate_field(field, value)
        yield Batch(name, columns, tuple(rows), str(path.relative_to(Path.cwd().resolve())), version)


def _sec_batches(*, spec, fields, ids, first, last, batch_size, memory, threads):
    """Read the existing candidate filing index, not a complete EDGAR universe."""
    if not ids or any(not re.fullmatch(r"[0-9]{1,10}", cik) for cik in ids):
        raise ValueError("Explicit SEC issuer CIKs required")
    ids = tuple(sorted({cik.zfill(10) for cik in ids}))
    path = private_path(filings.BASE / "index.duckdb")
    if not path.is_file():
        raise DataUnavailable("SEC filing index is missing")
    import duckdb
    if not re.fullmatch(r"[1-9][0-9]*(MB|GB)", memory) or type(threads) is not int or not 1 <= threads <= 32:
        raise ValueError("Invalid DuckDB resource limits")
    expected = {f"{y:04d}-Q{q}" for y in range(first.year, last.year + 1)
                for q in range(1, 5) if (first.year, (first.month - 1)//3 + 1)
                <= (y, q) <= (last.year, (last.month - 1)//3 + 1)}
    version = _version(path)
    with closing(duckdb.connect(str(path), read_only=True)) as con:
        con.execute(f"SET memory_limit = '{memory}'")
        con.execute(f"SET threads = {threads}")
        con.execute("BEGIN TRANSACTION")
        available = {row[0] for row in con.execute("SELECT quarter FROM partitions").fetchall()}
        missing = expected - available
        if missing:
            raise DataUnavailable("Missing SEC index quarters: " + ", ".join(sorted(missing)))
        relation = """(SELECT f.cik,f.filed_date,f.form,f.source_url,f.quarter,f.issuer,
            p.digest AS index_sha256,p.parser_version FROM filings f
            JOIN partitions p ON f.quarter=p.quarter)"""
        selected = spec.keys + (fields or spec.fields)
        sql, params = _sql(spec, selected, ids, first, last, relation)
        yield from _batches(con.execute(sql, params), name="sec_filings", spec=spec,
                            columns=selected, path=path, batch_size=batch_size, version=version)


def review_security(*, as_of: str, trade_date: str | None = None):
    """Reuse the evidence ledger's date-precision PIT resolution, read-only.

    Includes conflicts and blockers; never certifies universe completeness.
    Unlike price batches this existing snapshot materializes the visible ledger.
    """
    decision = _day(as_of)
    observed = _day(trade_date) if trade_date is not None else decision
    if observed > decision:
        raise AdmissionDenied("Cannot review a future observation")
    path = private_path(security_master.BASE / "ledger.sqlite3")
    if not path.is_file():
        raise DataUnavailable("Reviewed security evidence ledger is missing")
    report = security_master.snapshot(decision.isoformat(), observed.isoformat(), read_only=True)
    return {**report, "point_in_time_verified": False, "approved_for_model_training": False,
            "approved_for_backtest": False, "live_execution_enabled": False}


_SEC_FACT_COLUMNS = ("cik", "accession", "filed_date", "period_end",
                     "reported_shares_date", "reported_shares", "unadjusted_close")


def iter_fundamentals(path: Path, *, start: str, end: str, as_of: str,
                      purpose: str, symbols=(), batch_size: int = 2048):
    """Stream either existing provenance-bearing annual fundamental CSV format.

    These are close-derived values: the snapshot day itself is unavailable
    at a date-only decision cutoff. No forward fill or identity inference.
    """
    first, last, cutoff = _day(start), _day(end), _day(as_of)
    if purpose != "exploratory" or last >= cutoff:
        raise AdmissionDenied("Fundamental snapshots must precede decision date")
    if last < first or (last-first).days > 366:
        raise ValueError("Invalid fundamental date range")
    if type(batch_size) is not int or not 1 <= batch_size <= 16384:
        raise ValueError("batch_size must be 1..16384")
    if isinstance(symbols, str):
        raise ValueError("symbols must be a sequence")
    ids = tuple(symbols)
    if len(ids) > 128 or any(not isinstance(s, str) or not s or len(s) > 128 for s in ids):
        raise ValueError("Invalid provider identifiers")
    ids = set(ids)
    path = private_path(Path(path))
    if not path.is_file():
        raise DataUnavailable("Fundamental snapshot CSV missing")
    version = _version(path)
    origin = str(path.relative_to(Path.cwd().resolve()))
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        # Use the actual outputs of stock_fundamentals_import and
        # sec_fundamental_history, not generic undated ratio files.
        if header == FUNDAMENTAL_COLUMNS + _PROVENANCE_COLUMNS:
            sec = False
        elif header == FUNDAMENTAL_COLUMNS + _SEC_FACT_COLUMNS:
            sec = True
        else:
            raise DataQualityError("Unrecognized provenance-bearing fundamental schema")
        rows = []
        previous = None
        for row in reader:
            if None in row or any(v is None or not v.strip() for v in row.values()):
                raise DataQualityError("Malformed fundamental row")
            available = _day(row["as_of_date"])
            key = (available, row["symbol"])
            if previous is not None and key <= previous:
                raise DataQualityError("Fundamental snapshots must be sorted and unique")
            previous = key
            if available > last:
                continue
            if available < first or (ids and row["symbol"] not in ids):
                continue
            filed, period = _day(row["filed_date"]), _day(row["period_end"])
            if not period <= filed <= available:
                raise DataQualityError("Invalid fundamental filing chronology")
            if sec:
                if filed >= available or not re.fullmatch(r"[0-9]{10}", row["cik"]):
                    raise DataQualityError("Invalid SEC snapshot provenance")
                if not _day(row["reported_shares_date"]) <= filed:
                    raise DataQualityError("Shares observation is after filing")
                for name in ("reported_shares", "unadjusted_close"):
                    _validate_field("close", float(row[name]))
            else:
                accepted = datetime.fromisoformat(row["accepted_at"])
                if accepted.utcoffset() is None:
                    raise DataQualityError("Missing acceptance timezone")
                local = accepted.astimezone(ZoneInfo("America/New_York")).date()
                if not period <= local <= filed or local >= available:
                    raise DataQualityError("Acceptance chronology fails")
            values = {k: float(row[k]) for k in FUNDAMENTAL_COLUMNS[3:]}
            issue = fundamental_value_issue(values)
            if issue:
                raise DataQualityError(issue)
            rows.append(tuple(values.get(k, row[k]) for k in header))
            if len(rows) == batch_size:
                yield Batch("company_fundamentals", header, tuple(rows), origin, version)
                rows.clear()
        if rows:
            yield Batch("company_fundamentals", header, tuple(rows), origin, version)
    if _version(path) != version:
        raise DataQualityError("Fundamental source changed during scan")


def parquet_manifest(dataset: str, *, start: str, end: str):
    """Stream SHA-256 fingerprints from existing local partitions; no copying.

    The returned digests are content evidence, not a certificate of authenticity,
    coverage or data rights. Recompute after updates to detect revisions.
    """
    if dataset not in CATALOG or CATALOG[dataset].store != "parquet":
        raise ValueError("Manifest supports immutable Parquet inputs only")
    first, last = _day(start), _day(end)
    if last < first or (last-first).days > 366:
        raise ValueError("Invalid manifest date range")
    paths = tuple(_partitions(CATALOG[dataset], first, last))
    partitions = []
    for path in paths:
        version = _version(path)
        record = fingerprint(path)
        if _version(path) != version:
            raise DataQualityError("Partition changed while hashing")
        partitions.append({"path": str(path.relative_to(Path.cwd().resolve())),
                           "sha256": record["sha256"], "bytes": record["bytes"]})
    return {"dataset": dataset, "partitions": partitions, "coverage_verified": False,
            "approved_for_model_training": False, "approved_for_backtest": False,
            "live_execution_enabled": False}


def archive_plan(start_month: str, end_month: str):
    """Inspect monthly availability without downloading, writing or certifying."""
    start = _day(hf_stock5m.validate_month(start_month) + "-01")
    end = _day(hf_stock5m.validate_month(end_month) + "-01")
    if start > end or (end.year-start.year)*12 + end.month-start.month > 119:
        raise ValueError("Plan at most 120 months")
    rows = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        month = f"{y:04d}-{m:02d}"
        raw = hf_stock5m.raw_path(month)
        bars = hf_stock5m.output_path(month, "rth")
        factors = factor_db._path(month, "hf", "rth", "factors")
        _, qc = hf_stock5m.quality_paths(bars)
        rows.append({"month": month,
                     "within_advertised_archive": hf_stock5m.EARLIEST_MONTH <= month <= hf_stock5m.LATEST_ADVERTISED_MONTH,
                     "raw_present": raw.is_file(), "bars_present": bars.is_file(),
                     "quarantine_report_present": qc.is_file(),
                     "factor_present": factors.is_file()})
        y, m = (y+1, 1) if m == 12 else (y, m+1)
    return {"months": rows, "mode": "plan_only", "coverage_verified": False,
            "approved_for_model_training": False, "live_execution_enabled": False}


def audit_archive(start_month: str, end_month: str, *, resume: bool = True,
                  dry_run: bool = True):
    """Delegate to the existing resumable audit without claiming admission."""
    plan = archive_plan(start_month, end_month)
    if any(not row["within_advertised_archive"] for row in plan["months"]):
        raise DataUnavailable("Requested audit extends beyond advertised archive")
    if type(resume) is not bool or type(dry_run) is not bool:
        raise ValueError("resume and dry_run must be boolean")
    return hf_stock5m_batch.run(start_month, end_month, session="rth",
                               resume=resume, dry_run=dry_run)


def review_broker_capture(capture_path: Path, mapping_path: Path, *,
                          allow_fixture: bool = False, now_utc=None):
    """Read existing private capture/mapping files; no broker connection or orders."""
    from atlaspm.execution.ibkr_gateway import _json_no_nan
    from atlaspm.execution.ibkr_mapping import diagnose_ibkr_contract_mapping
    if type(allow_fixture) is not bool:
        raise ValueError("allow_fixture must be boolean")
    def load(path):
        path = private_path(Path(path))
        if not path.is_file():
            raise DataUnavailable("Private broker review input missing")
        with path.open("rb") as handle:
            data = handle.read(12 * 1024 * 1024 + 1)
        if len(data) > 12 * 1024 * 1024:
            raise DataQualityError("Private broker review input exceeds 12 MiB")
        result = _json_no_nan(data)
        if not isinstance(result, dict):
            raise DataQualityError("Expected broker review JSON object")
        return result
    capture = load(capture_path)
    fixture = capture.get("source") == "synthetic_ibkr_fixture"
    if fixture and not allow_fixture:
        raise AdmissionDenied("Synthetic broker captures require explicit fixture mode")
    if capture.get("live_execution_enabled") is not False:
        raise AdmissionDenied("Expected read-only broker capture")
    report = diagnose_ibkr_contract_mapping(capture, load(mapping_path), now_utc=now_utc)
    return {**report, "synthetic_fixture": fixture, "local_only": True,
            "approved_for_model_training": False, "approved_for_backtest": False,
            "live_execution_enabled": False}


def iter_batches(dataset: str, *, start: str, end: str, columns=(), symbols=(),
                 purpose: str, as_of=None, batch_size: int = 2048,
                 database: Path = DB_PATH, memory: str = "256MB", threads: int = 1):
    """Yield read-only exploratory batches; exhaust or close the iterator.

    Date ranges are inclusive. Daily/5m date fields retain their source calendar;
    timestamp-only feeds use UTC dates. Keys and source (SQLite) are mandatory.
    A size/mtime hint is not a content hash or an immutable snapshot ID.
    No rows is not proof of completeness. An intraday as_of is a nominal
    exchange-session and bucket-end cutoff, not vendor publication verification.
    """
    if purpose != "exploratory" or (as_of is not None and dataset not in
                                      {"sec_filings", "equity_1m", "equity_5m"}):
        raise AdmissionDenied("Certified point-in-time access is not available")
    if dataset not in CATALOG:
        raise DataUnavailable(f"No connected adapter: {dataset}")
    spec = CATALOG[dataset]
    first, last = _day(start), _day(end)
    if last < first or (last - first).days > 366:
        raise ValueError("Use an inclusive range of at most 367 days")
    if type(batch_size) is not int or not 1 <= batch_size <= 16384:
        raise ValueError("batch_size must be 1..16384")
    if isinstance(columns, str) or isinstance(symbols, str):
        raise ValueError("columns and symbols must be sequences, not strings")
    fields = tuple(columns)
    if len(fields) != len(set(fields)) or any(f not in spec.fields for f in fields):
        raise ValueError("Unknown, repeated, or non-projectable field")
    selected = spec.keys + (fields or spec.fields) + (("source",) if spec.store == "sqlite" else ())
    cutoff = None
    if as_of is not None and dataset in {"equity_1m", "equity_5m"}:
        decision, cutoff = _decision_ms(as_of)
        if last > decision.astimezone(ZoneInfo("America/New_York")).date():
            raise AdmissionDenied("Requested market date is after decision time")
        selected += ("bar_end_ms",)
    ids = tuple(symbols)
    if len(ids) > 128 or any(not isinstance(s, str) or not s or len(s) > 128 for s in ids):
        raise ValueError("At most 128 nonempty provider identifiers")
    if spec.store == "sec_index":
        if as_of is None or last >= _day(as_of):
            raise AdmissionDenied("SEC filing dates must precede the explicit decision date")
        yield from _sec_batches(spec=spec, fields=fields, ids=ids, first=first, last=last,
                                batch_size=batch_size, memory=memory, threads=threads)
    elif spec.store == "sqlite":
        path = private_path(database)
        if not path.is_file():
            raise DataUnavailable("Market database is missing")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            sql, params = _sql(spec, selected, ids, first, last, spec.relation)
            yield from _batches(con.execute(sql, params), name=dataset, spec=spec,
                                columns=selected, path=path, batch_size=batch_size,
                                version=_version(path))
    else:
        paths = tuple(_partitions(spec, first, last))
        # Existing DuckDB configuration and spill path; no new data store.
        with closing(factor_db._db(memory, threads)) as con:
            for path in paths:
                version = _version(path)
                relation = (_raw_minute_relation(con, path) if spec.relation == "hf_raw"
                            else factor_db._files_sql([path]))
                if spec.relation == "hf_raw":
                    month = path.stem.removeprefix("ohlcv_")
                    if con.execute(f"""SELECT 1 FROM {relation}
                        WHERE strftime(trade_date_et, '%Y-%m') <> ? LIMIT 1""",
                                   [month]).fetchone():
                        raise DataQualityError("Raw RTH date outside declared partition")
                if cutoff is not None:
                    relation = _session_relation(con, spec, path, relation, cutoff)
                sql, params = _sql(spec, selected, ids, first, last, relation)
                yield from _batches(con.execute(sql, params), name=dataset, spec=spec,
                                    columns=selected, path=path, batch_size=batch_size,
                                    version=version)
                if _version(path) != version:
                    raise DataQualityError("Partition changed during scan; discard query results")
