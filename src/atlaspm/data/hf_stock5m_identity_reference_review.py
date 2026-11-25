"""Reference interval triage for historical SEC leads, not identity admission.

External rows are reviewer-declared normalized assertions tied to retained
raw bytes. Hash checks do not authenticate the publisher, its rights, its
normalization, or independence. No ledger or training mutations.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
from urllib.parse import urlsplit

from atlaspm.data import hf_stock5m_security_master_v53 as ledger
from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data.hf_stock5m_certification_v5 import EXCHANGES, ROOT, fingerprint
from atlaspm.data.market_db import private_path

FIELDS = {
    "reference_id", "source_family", "source_url", "evidence_path",
    "evidence_sha256", "reviewer", "known_date", "cik", "symbol",
    "source_security_key", "exchange", "valid_from", "valid_to",
}
MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 20000
MAX_PER_SYMBOL = 100
# Unambiguous single-venue text only. Combined venues are never reduced to
# a primary exchange; a market name is not proof of primary listing.
MIC = {
    "NEW YORK STOCK EXCHANGE": "XNYS", "NYSE": "XNYS",
    "AMERICAN STOCK EXCHANGE": "XASE", "NYSE AMERICAN": "XASE",
    "NASDAQ": "XNAS", "NASDAQ STOCK MARKET": "XNAS",
    "NASDAQ NATIONAL MARKET": "XNAS", "NASDAQ NATIONAL MARKET SYSTEM": "XNAS",
    "NASDAQ SMALLCAP MARKET": "XNAS", "NASDAQ SMALL CAP INDEX": "XNAS",
    "THE NASDAQ STOCK MARKET LLC": "XNAS", "THE NASDAQ STOCK MARKET": "XNAS",
    "NASDAQ CAPITAL MARKET": "XNAS", "NASDAQ GLOBAL MARKET": "XNAS",
    "NASDAQ GLOBAL SELECT MARKET": "XNAS", "NYSE ARCA": "ARCX",
}


def _date(value):
    return ledger._date(value)


def _url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError("Invalid reference HTTPS URL")
    u = urlsplit(value)
    host = u.hostname
    if (u.scheme != "https" or not host or "." not in host
            or u.username is not None or u.password is not None or u.fragment
            or host == "sec.gov" or host.endswith(".sec.gov")):
        raise ValueError("Require a declared non-SEC HTTPS source")
    return host


def _validate(row, manifest, checked):
    if not isinstance(row, dict) or set(row) != FIELDS:
        raise ValueError("Unexpected external reference fields")
    r = dict(row)
    for key in ("reference_id", "source_family", "reviewer", "source_security_key"):
        ledger._token(r[key])
    if not isinstance(r["cik"], str) or not r["cik"].isascii() or not r["cik"].isdigit() or len(r["cik"]) != 10:
        raise ValueError("Require explicit ten-digit CIK; never infer from ticker")
    if not isinstance(r["symbol"], str) or not ledger.SYMBOL.fullmatch(r["symbol"]):
        raise ValueError("Invalid reference symbol")
    if r["exchange"] not in EXCHANGES:
        raise ValueError("Unsupported reference MIC")
    _date(r["known_date"])
    _date(r["valid_from"])
    if r["valid_to"] is not None and _date(r["valid_to"]) <= r["valid_from"]:
        raise ValueError("Invalid exclusive reference end")
    r["source_host"] = _url(r["source_url"])
    if not isinstance(r["evidence_sha256"], str) or not ledger.SHA.fullmatch(r["evidence_sha256"]):
        raise ValueError("Invalid reference evidence SHA-256")
    if not isinstance(r["evidence_path"], str):
        raise ValueError("Invalid evidence path")
    p = private_path(Path(r["evidence_path"]), exists=True)
    # This reviewer input and all generated pipeline artifacts are excluded
    # from external source evidence, even if they have a different digest.
    if p == manifest or p.is_relative_to(private_path(ROOT)):
        raise ValueError("Require retained external raw evidence, not pipeline artifacts")
    key = str(p)
    if key not in checked:
        checked[key] = fingerprint(p)
    if checked[key]["sha256"] != r["evidence_sha256"]:
        raise ValueError("External evidence hash mismatch")
    r["evidence_path"] = str(p.relative_to(Path.cwd().resolve()))
    return r


def load_reference(filename):
    path = private_path(Path(filename), exists=True)
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("External reference exceeds 16 MiB batch budget")
    checked, rows, ids = {}, [], set()
    counts = Counter()
    for n, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            if len(rows) >= MAX_ROWS:
                raise ValueError("External reference exceeds row budget")
            r = _validate(json.loads(line), path, checked)
            if r["reference_id"] in ids:
                raise ValueError("Duplicate reference ID")
            ids.add(r["reference_id"])
            counts[r["symbol"]] += 1
            if counts[r["symbol"]] > MAX_PER_SYMBOL:
                raise ValueError("Too many intervals for one symbol; use a scoped batch")
            rows.append(r)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"External reference line {n}: {exc}") from exc
    if not rows:
        raise ValueError("Empty external reference")
    # Catch changed source objects and normalized input during validation.
    for key, old in checked.items():
        if fingerprint(key) != old:
            raise ValueError("External reference source changed")
    if fingerprint(path)["sha256"] != ev._sha(raw):
        raise ValueError("External reference input changed")
    return rows, {"sha256": ev._sha(raw), "rows": len(rows),
                  "raw_objects": len(checked), "path": str(path)}


def _anchor(candidate):
    if candidate["kind"] == "CHANGE_OLD":
        return (date.fromisoformat(candidate["reported_change_date"]) - timedelta(days=1)).isoformat()
    if candidate["kind"] == "CHANGE_NEW":
        return _date(candidate["reported_change_date"])
    return _date(candidate["filed_date"])


def _mic(candidate):
    source = candidate["source_lead"]
    if candidate["kind"] == "COVER_OBSERVATION":
        return source.get("exchange_mic_candidate")
    return MIC.get((candidate["reported_exchange"] or "").upper())


def _effective(rows, day, cutoff):
    return [r for r in rows if r["known_date"] < cutoff
            and r["valid_from"] <= day
            and (r["valid_to"] is None or day < r["valid_to"])]


def _classify(candidate, effective, competing):
    if competing:
        return "SEC_CIK_COLLISION_REVIEW_REQUIRED"
    if not effective:
        return "NO_VISIBLE_REFERENCE_INTERVAL"
    if {r["cik"] for r in effective} != {candidate["cik"]}:
        return "REFERENCE_CIK_CONFLICT"
    # Different IDs within the same family at the same point are ambiguous.
    keys = defaultdict(set)
    for r in effective:
        keys[r["source_family"]].add(r["source_security_key"])
    if any(len(k) > 1 for k in keys.values()):
        return "REFERENCE_SECURITY_KEY_CONFLICT"
    mic = _mic(candidate)
    if mic not in EXCHANGES:
        return "SEC_EXCHANGE_UNRESOLVED"
    if {r["exchange"] for r in effective} != {mic}:
        return "REFERENCE_EXCHANGE_CONFLICT"
    return "DECLARED_CIK_SYMBOL_MIC_AGREEMENT"


def review(candidates, observed_days, *, as_of, reference_file=None, scan_complete=False):
    """Separate retrospective point comparison from strictly prior-known overlap.

    Point agreement is not class identity. Daily overlap counts distinct
    symbol-days, never multiplied by filings. Changes are point checks only:
    no forward/backward interval is inferred from a transition sentence.
    """
    as_of = _date(as_of)
    references, manifest = load_reference(reference_file) if reference_file else ([], None)
    by_symbol, claims = defaultdict(list), defaultdict(set)
    for r in references:
        by_symbol[r["symbol"]].append(r)
    for c in candidates:
        if c["filed_date"] < as_of:
            claims[c["symbol"]].add(c["cik"])
    points, queues = [], []
    point_counts = Counter()
    day_outcomes = defaultdict(set)
    for c in candidates:
        symbol = c["symbol"]
        anchor = _anchor(c)
        competing = len(claims[symbol]) > 1
        rows = by_symbol[symbol]
        visible = _effective(rows, anchor, as_of)
        if c["filed_date"] >= as_of or anchor >= as_of:
            status = "SEC_LEAD_NOT_VISIBLE_AT_REVIEW_CUTOFF"
            visible = []
        else:
            status = _classify(c, visible, competing)
        points.append({
            "cik": c["cik"], "symbol": symbol, "kind": c["kind"],
            "anchor_calendar_date": anchor, "anchor_is_not_effective_date": True,
            "filed_date": c["filed_date"], "source_sha256": c["source_sha256"],
            "source_url": c["source_url"], "status": status,
            "matched_reference_ids": sorted(r["reference_id"] for r in visible),
            "reference_source_families": sorted({r["source_family"] for r in visible}),
            "security_id": None, "valid_from": None, "valid_to": None,
        })
        point_counts[status] += 1
        # Review requests are scoped to the exact filing and point, not
        # manufactured intervals or automatic V5.3 claims.
        queues.append({
            "cik": c["cik"], "issuer": c["issuer"], "symbol": symbol,
            "kind": c["kind"], "anchor_calendar_date": anchor,
            "reported_exchange": c["reported_exchange"],
            "sec_source_url": c["source_url"], "sec_source_sha256": c["source_sha256"],
            "required": ["historical_share_class_identifier", "primary_venue_evidence",
                         "validity_boundaries", "publication_date", "raw_external_source"],
            "status": status,
        })
        if c["kind"] not in ("LISTING_TEXT", "COVER_OBSERVATION"):
            continue
        for day in observed_days.get(symbol, {}):
            if day >= as_of:
                continue
            key = (symbol, day)
            if c["filed_date"] >= day:
                day_outcomes[key].add("SEC_NOT_PRIOR_KNOWN")
                continue
            # Do not project a later SEC claim about a reused code backwards.
            prior_ciks = {x["cik"] for x in candidates
                          if x["symbol"] == symbol and x["filed_date"] < day}
            day_outcomes[key].add(_classify(c, _effective(rows, day, day), len(prior_ciks) > 1))
    day_counts = Counter()
    for outcomes in day_outcomes.values():
        considered = outcomes - {"SEC_NOT_PRIOR_KNOWN"}
        if not considered:
            status = "SEC_NOT_PRIOR_KNOWN"
        elif considered == {"DECLARED_CIK_SYMBOL_MIC_AGREEMENT"}:
            status = "PRIOR_KNOWN_DECLARED_OVERLAP"
        elif len(considered) == 1:
            status = next(iter(considered))
        else:
            status = "MULTIPLE_CANDIDATE_OUTCOMES_REVIEW_REQUIRED"
        day_counts[status] += 1
    return {
        "schema": "atlaspm_historical_reference_review_v1",
        "as_of_exclusive": as_of, "reference_manifest": manifest,
        "reference_rows": references, "point_status_counts": dict(point_counts),
        "point_reviews": points, "evidence_requests": queues,
        "daily_status_counts": dict(day_counts),
        "observed_listing_candidate_symbol_days": len(day_outcomes),
        "prior_known_declared_overlap_symbol_days": day_counts["PRIOR_KNOWN_DECLARED_OVERLAP"],
        "scan_complete": scan_complete, "coverage_verified": False,
        "independent_source_verified": False, "historical_security_identity_verified": False,
        "approved_for_model_training": False, "approved_for_backtest": False,
        "model_registry_admission": False, "live_execution_enabled": False,
        "broker_order_submission_enabled": False, "claims_ingested": 0,
        "certified_identities": 0,
        "status": "EXTERNAL_REFERENCE_REVIEW_REQUIRED" if references else "EXTERNAL_REFERENCE_REQUIRED",
        "warning": ("Declared interval and point agreement is not verified continuity, "
                    "primary venue, share class, independent source, universe coverage or authorization. "
                    "External data normalization and provenance require independent review."),
    }
