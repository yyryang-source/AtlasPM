"""Read-only, bounded access to existing market stores.

Observed dates and provider symbols are not certified publication timestamps
or permanent security IDs. No training, backtest certification or execution
permissions are issued by this exploratory interface.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import isfinite
from pathlib import Path
import sqlite3
from types import MappingProxyType

from atlaspm.data import factor_db, hf_stock5m
from atlaspm.data.market_db import DB_PATH, private_path


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
    **{f"factors_{source}": Dataset("parquet", "factors", ("symbol", "trade_date"),
                                  ("close", "volume_shares", "observed_5m_bars",
                                   "observed_1m_bars", "last_observed_bar_ms") + factor_db.FEATURES,
                                  source=source) for source in factor_db.SOURCES},
})

# Explicitly unavailable through this API, not fabricated empty tables.
UNCONNECTED = frozenset({
    "equity_1m", "equity_realtime", "company_fundamentals", "sec_filings",
    "macro", "security_identity", "corporate_actions", "ibkr_accounts",
    "ibkr_positions", "option_contracts", "bond_terms",
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
        path = (hf_stock5m.output_path(month, "rth") if spec.relation == "hf"
                else factor_db._path(month, spec.source, "rth", "factors"))
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


def iter_batches(dataset: str, *, start: str, end: str, columns=(), symbols=(),
                 purpose: str, as_of=None, batch_size: int = 2048,
                 database: Path = DB_PATH, memory: str = "256MB", threads: int = 1):
    """Yield read-only exploratory batches; exhaust or close the iterator.

    Date ranges are inclusive. Daily/5m date fields retain their source calendar;
    timestamp-only feeds use UTC dates. Keys and source (SQLite) are mandatory.
    A size/mtime hint is not a content hash or an immutable snapshot ID.
    No rows is not proof of completeness. No as-of filtering is simulated.
    """
    if purpose != "exploratory" or as_of is not None:
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
    ids = tuple(symbols)
    if len(ids) > 128 or any(not isinstance(s, str) or not s or len(s) > 128 for s in ids):
        raise ValueError("At most 128 nonempty provider identifiers")
    if spec.store == "sqlite":
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
                sql, params = _sql(spec, selected, ids, first, last,
                                   factor_db._files_sql([path]))
                yield from _batches(con.execute(sql, params), name=dataset, spec=spec,
                                    columns=selected, path=path, batch_size=batch_size,
                                    version=version)
                if _version(path) != version:
                    raise DataQualityError("Partition changed during scan; discard query results")
