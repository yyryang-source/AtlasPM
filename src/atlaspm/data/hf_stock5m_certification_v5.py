"""V5 local evidence validation. No vendor claims or training approvals are inferred."""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path

from atlaspm.data import hf_stock5m as hf
from atlaspm.data import hf_stock5m_session_v4 as v4
from atlaspm.data.market_db import private_path

SCHEMA = "atlaspm_hf_certification_v5"
IDENTITY_FIELDS = ("security_id", "symbol", "exchange", "valid_from", "valid_to",
                   "known_date", "source", "evidence_id")
ACTION_FIELDS = ("event_id", "security_id", "kind", "effective_date", "known_date",
                 "value", "currency", "source", "evidence_id")
EXCHANGES = {"XNYS", "XNAS", "XASE", "ARCX", "BATS", "IEXG"}
ROOT = hf.ROOT / "quality" / "certification_v5"


@dataclass(frozen=True)
class Identity:
    security_id: str
    symbol: str
    exchange: str
    valid_from: date
    valid_to: date | None
    known_date: date
    source: str
    evidence_id: str


@dataclass(frozen=True)
class Action:
    event_id: str
    security_id: str
    kind: str
    effective_date: date
    known_date: date
    value: Decimal
    currency: str
    source: str
    evidence_id: str


def _date(s):
    d = date.fromisoformat(s)
    if d.isoformat() != s:
        raise ValueError("Dates must use YYYY-MM-DD")
    return d


def _read(path, fields):
    path = private_path(Path(path), exists=True)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != fields:
            raise ValueError(f"Unexpected CSV header: {path.name}; expected {fields}")
        for i, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"Malformed CSV row {i}: {path.name}")
            row = {k: v.strip() for k, v in row.items()}
            allowed_empty = {"valid_to"} if fields == IDENTITY_FIELDS else {"currency"}
            if any(not v for k, v in row.items() if k not in allowed_empty):
                raise ValueError(f"Blank required field on row {i}: {path.name}")
            yield row


def load_identities(path):
    result = []
    for r in _read(path, IDENTITY_FIELDS):
        start = _date(r["valid_from"])
        end = _date(r["valid_to"]) if r["valid_to"] else None
        if end is not None and end <= start:
            raise ValueError("Identity end must be exclusive and later than start")
        if r["symbol"] != r["symbol"].upper() or len(r["symbol"]) > 32:
            raise ValueError("Invalid canonical symbol")
        if r["exchange"] not in EXCHANGES:
            raise ValueError("Unsupported/unknown exchange MIC")
        result.append(Identity(r["security_id"], r["symbol"], r["exchange"],
                               start, end, _date(r["known_date"]), r["source"], r["evidence_id"]))
    if not result:
        raise ValueError("Empty identity ledger")
    # No simultaneous ticker assignments or simultaneous aliases for one ID.
    # Adjacent intervals are allowed, including a ticker reused by another ID.
    for field in ("symbol", "security_id"):
        groups = defaultdict(list)
        for item in result:
            groups[getattr(item, field)].append(item)
        for key, group in groups.items():
            group.sort(key=lambda x: x.valid_from)
            for left, right in zip(group, group[1:]):
                if left.valid_to is None or right.valid_from < left.valid_to:
                    raise ValueError(f"Overlapping {field} intervals: {key}")
    return result


def load_actions(path, identities):
    ids = {i.security_id for i in identities}
    result, seen, economic_keys = [], set(), set()
    for r in _read(path, ACTION_FIELDS):
        effective = _date(r["effective_date"])
        known = _date(r["known_date"])
        if r["event_id"] in seen or r["security_id"] not in ids:
            raise ValueError("Duplicate event ID or unknown security ID")
        if not any(i.security_id == r["security_id"] and i.valid_from <= effective
                   and (i.valid_to is None or effective < i.valid_to) for i in identities):
            raise ValueError("Action outside security identity lifetime")
        try:
            value = Decimal(r["value"])
        except InvalidOperation as exc:
            raise ValueError("Invalid action value") from exc
        if not value.is_finite() or value <= 0:
            raise ValueError("Action value must be finite and positive")
        if r["kind"] == "SPLIT":
            if r["currency"] or value == 1:
                raise ValueError("Split ratio must be non-unit new/old shares; no currency")
        elif r["kind"] == "CASH_DIVIDEND":
            if r["currency"] != "USD":
                raise ValueError("Only USD cash dividends are supported")
        else:
            raise ValueError("Unsupported corporate action; do not silently discard")
        key = (r["security_id"], r["kind"], effective)
        if key in economic_keys:
            raise ValueError("Ambiguous same-day action; consolidate with evidence first")
        seen.add(r["event_id"])
        economic_keys.add(key)
        result.append(Action(r["event_id"], r["security_id"], r["kind"], effective,
                             known, value, r["currency"], r["source"], r["evidence_id"]))
    return result


def resolve(identities, symbol, trade_date, decision_date):
    """Date-precision evidence: same-day publication is deliberately unavailable."""
    if decision_date < trade_date:
        raise ValueError("Cannot resolve a future observation")
    return next((i for i in identities if i.symbol == symbol
                 and i.valid_from <= trade_date
                 and (i.valid_to is None or trade_date < i.valid_to)
                 and i.known_date < decision_date), None)


def split_adjustment(actions, security_id, bar_date, as_of, *, raw_unadjusted=False):
    """Research calculator only; never apply to unknown/already adjusted prices.

    Price multiplier and share-volume multiplier for backward split adjustment.
    A late-reported effective split blocks adjustment instead of silently leaking.
    Cash dividends are ledgered separately, not included in these multipliers.
    """
    if not raw_unadjusted or bar_date > as_of:
        raise ValueError("Require independently verified unadjusted input and valid dates")
    ratio = Decimal(1)
    for a in actions:
        if a.security_id == security_id and a.kind == "SPLIT" and bar_date < a.effective_date <= as_of:
            if a.known_date >= as_of:
                raise ValueError("Effective split not known before decision date")
            ratio *= a.value
    return Decimal(1) / ratio, ratio


def fingerprint(path):
    p = private_path(Path(path), exists=True)
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": str(p), "sha256": h.hexdigest(), "bytes": p.stat().st_size}


def scan_month(month, identities):
    """Check observed full-NYSE-slot symbol/day mapping, not expected-universe coverage."""
    source = hf.output_path(month, "rth")
    before = v4._signature(source)
    cx = hf._open_duckdb()
    try:
        view = v4.install_research_view(cx, month)
        cx.execute("""CREATE TEMP TABLE identity_v5(
            security_id VARCHAR, symbol VARCHAR, exchange VARCHAR,
            valid_from DATE, valid_to DATE, known_date DATE)""")
        cx.executemany("INSERT INTO identity_v5 VALUES (?,?,?,?,?,?)",
                       [(i.security_id, i.symbol, i.exchange, i.valid_from,
                         i.valid_to, i.known_date) for i in identities])
        cx.execute(f"""CREATE TEMP TABLE daily_v5 AS
            SELECT symbol, trade_date_et, count(*) AS bars
            FROM {view} GROUP BY 1,2""")
        cx.execute("""CREATE TEMP VIEW mapping_v5 AS
            SELECT d.*, i.security_id, i.exchange,
              CASE WHEN i.security_id IS NULL THEN 'NO_EFFECTIVE_IDENTITY'
                   WHEN i.known_date >= d.trade_date_et THEN 'NOT_KNOWN_BEFORE_SESSION'
                   ELSE 'MAPPED' END AS status
            FROM daily_v5 d LEFT JOIN identity_v5 i
              ON d.symbol=i.symbol AND d.trade_date_et>=i.valid_from
              AND (i.valid_to IS NULL OR d.trade_date_et<i.valid_to)""")
        totals = cx.execute("""SELECT status, count(*), sum(bars)
            FROM mapping_v5 GROUP BY status ORDER BY status""").fetchall()
        examples = cx.execute("""SELECT symbol, trade_date_et, status, bars
            FROM mapping_v5 WHERE status <> 'MAPPED'
            ORDER BY trade_date_et,symbol LIMIT 25""").fetchall()
    finally:
        cx.close()
    if before != v4._signature(source):
        raise ValueError("Canonical source changed during scan")
    return {"month": month, "source_signature": before,
            "mapping_counts": {k: {"symbol_days": int(n), "bars": int(b)}
                               for k, n, b in totals},
            "unmapped_examples": [{"symbol": s, "date": d.isoformat(),
                                   "status": t, "bars": int(n)}
                                  for s, d, t, n in examples]}


def run(start="2000-01", end="2026-03", *, identity_csv=None,
        actions_csv=None, scan=False):
    chosen = hf.months(start, end, 0)
    if not chosen:
        raise ValueError("No archive months selected")
    report = {"schema": SCHEMA, "period": [chosen[0], chosen[-1]],
              "months_selected": len(chosen), "scan_requested": scan,
              "evidence": {}, "blockers": [], "months": [], **v4.GUARDS}
    ids = None
    if identity_csv is None:
        report["blockers"].append("MISSING_HISTORICAL_IDENTITY_LEDGER")
    else:
        before = fingerprint(identity_csv)
        ids = load_identities(identity_csv)
        if fingerprint(identity_csv) != before:
            raise ValueError("Identity ledger changed during read")
        report["evidence"]["identities"] = {**before, "rows": len(ids)}
    if actions_csv is None:
        report["blockers"].append("MISSING_CORPORATE_ACTION_LEDGER")
    elif ids is None:
        report["blockers"].append("IDENTITY_LEDGER_REQUIRED_BEFORE_ACTION_VALIDATION")
    else:
        before = fingerprint(actions_csv)
        actions = load_actions(actions_csv, ids)
        if fingerprint(actions_csv) != before:
            raise ValueError("Action ledger changed during read")
        report["evidence"]["corporate_actions"] = {**before, "rows": len(actions),
            "split_events": sum(a.kind == "SPLIT" for a in actions),
            "cash_dividends": sum(a.kind == "CASH_DIVIDEND" for a in actions)}
    if scan and ids is not None:
        for month in chosen:
            try:
                report["months"].append(scan_month(month, ids))
            except Exception as exc:
                report["months"].append({"month": month, "error": f"{type(exc).__name__}: {exc}"})
    if not scan or ids is None:
        report["blockers"].append("OBSERVED_IDENTITY_COVERAGE_NOT_SCANNED")
    elif any("error" in m or not m["mapping_counts"] or
             any(k != "MAPPED" for k in m["mapping_counts"]) for m in report["months"]):
        report["blockers"].append("IDENTITY_MAPPING_GAPS_OR_SCAN_ERRORS")
    # These require external corroboration; a self-authored CSV cannot certify them.
    report["blockers"].extend(["SOURCE_EVIDENCE_AUTHENTICITY_UNVERIFIED",
        "HISTORICAL_UNIVERSE_COMPLETENESS_UNVERIFIED", "CORPORATE_ACTION_COMPLETENESS_UNVERIFIED",
        "PRICE_ADJUSTMENT_SEMANTICS_UNVERIFIED", "INDEPENDENT_PRICE_RECONCILIATION_REQUIRED",
        "DATA_USAGE_RIGHTS_UNVERIFIED", "VENUE_SPECIFIC_SESSIONS_UNVERIFIED"])
    report["status"] = "EVIDENCE_REVIEW_REQUIRED"
    report["months_scanned"] = sum("mapping_counts" in m for m in report["months"])
    dest = private_path(ROOT / "latest.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.part")
    report["summary_path"] = str(dest)
    try:
        tmp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return report
