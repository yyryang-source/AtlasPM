"""Authorized news metadata import and deterministic local event research.

No unofficial endpoint, page scraping, paywall bypass, article-body ingestion,
or automatic external AI egress. Input is an operator-normalized licensed feed
export, not a claim that either vendor's proprietary wire schema is known.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from atlaspm.data.market_db import private_path
from atlaspm.data.provider_policy import local_rights
from atlaspm.data.sec_download import _atomic_write

_PROVIDERS = frozenset({"yahoo_finance", "bloomberg"})
_RULES = (
    ("monetary_policy", 5, r"\b(federal reserve|fomc|interest rate|rate cut|rate hike|central bank|ecb)\b"),
    ("systemic_risk", 5, r"\b(default|bankruptcy|bank failure|liquidity crisis|sanctions|ceasefire)\b"),
    ("macro_release", 4, r"\b(inflation|cpi|pce|payrolls|gdp|unemployment|jobs report)\b"),
    ("corporate_action", 4, r"\b(acquisition|merger|takeover|spin-off|stock split|buyback)\b"),
    ("earnings", 4, r"\b(earnings|guidance|revenue|quarterly results|profit warning)\b"),
    ("regulatory", 3, r"\b(antitrust|lawsuit|regulator|sec investigation|tariff)\b"),
    ("analyst", 2, r"\b(upgrade|downgrade|price target|analyst rating)\b"),
)
_FLAGS = {"research_only": True, "point_in_time_verified": False,
          "approved_for_model_training": False, "approved_for_backtest": False,
          "live_execution_enabled": False, "external_ai_allowed": False}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return sha256(_canonical(value).encode()).hexdigest()


def _time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid news timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("News timestamps require timezone")
    return stamp.astimezone(UTC)


def _str(value, maximum):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid news metadata")
    return value.strip()


def _url(value):
    p = urlsplit(_str(value, 2048))
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Expected public HTTPS article link")
    # No query credentials or tracking tokens are stored. Some vendor links
    # require query parameters; those must be normalized to permanent links.
    return urlunsplit(("https", p.hostname.lower(), p.path or "/", "", ""))


def _metadata(row, observed):
    if not isinstance(row, dict) or set(row) != {"id", "title", "published_at", "source", "url", "symbols", "sectors"}:
        raise ValueError("Expected licensed normalized metadata fields")
    published = _time(row["published_at"])
    if published > observed:
        raise ValueError("Publication after local observation")
    symbols, sectors = row["symbols"], row["sectors"]
    if (not isinstance(symbols, list) or len(symbols) > 64 or
            any(not isinstance(s, str) or not re.fullmatch(r"[A-Z0-9.:-]{1,24}", s) for s in symbols)
            or not isinstance(sectors, list) or len(sectors) > 32):
        raise ValueError("Invalid vendor associations")
    return {"id": _str(row["id"], 128), "title": _str(row["title"], 1000),
            "published_at": published.isoformat(), "source": _str(row["source"], 256),
            "url": _url(row["url"]), "symbols": sorted(set(symbols)),
            "sectors": sorted({_str(s, 80) for s in sectors})}


def _archive_path(provider, month, directory):
    if provider not in _PROVIDERS or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("Invalid news provider or month")
    return private_path(Path(directory) / provider / (month + ".jsonl"))


def _records(path, provider, month):
    if not path.exists():
        return []
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("News archive exceeds monthly byte budget; review retention")
    result = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            stamp = _time(row["observed_at"])
            if (row["provider"] != provider or stamp.strftime("%Y-%m") != month or
                    row["revision_sha256"] != _digest(row["metadata"]) or
                    _metadata(row["metadata"], stamp) != row["metadata"]):
                raise ValueError("News archive integrity/scope failure")
            result.append(row)
            if len(result) > 10000:
                raise ValueError("News archive exceeds monthly record budget")
    return result


def ingest(provider, input_file, *, directory=Path("local_data/news")):
    """Persist only licensed metadata, revision-deduped by content.

    One UTC observation-month partition per provider. A fresh local receipt
    never pretends to be an original historical wire timestamp. Single writer
    lock protects the read-modify-replace; abandoned lock needs operator review.
    """
    if provider not in _PROVIDERS:
        raise ValueError("Only designated news providers allowed")
    local_rights(provider, retention=True)
    source = private_path(Path(input_file), exists=True)
    if source.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("News input exceeds 4 MiB")
    with source.open("rb") as handle:
        raw = handle.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("News input exceeds 4 MiB")
    data = json.loads(raw)
    if not isinstance(data, list) or len(data) > 1000:
        raise ValueError("News input requires at most 1000 metadata rows")
    now = datetime.now(UTC)
    prepared = [_metadata(row, now) for row in data]
    month = now.strftime("%Y-%m")
    path = _archive_path(provider, month, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    # Never remove an existing lock belonging to another writer.
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        rows = _records(path, provider, month)
        known = {row["revision_sha256"] for row in rows}
        added = 0
        for meta in prepared:
            revision = _digest(meta)
            if revision in known:
                continue
            rows.append({"provider": provider, "observed_at": now.isoformat(),
                         "revision_sha256": revision, "input_sha256": sha256(raw).hexdigest(),
                         "metadata": meta})
            known.add(revision)
            added += 1
        if added:
            if len(rows) > 10000:
                raise ValueError("News archive exceeds monthly record budget")
            body = ("\n".join(_canonical(row) for row in rows) + "\n").encode()
            if len(body) > 16 * 1024 * 1024:
                raise ValueError("News archive exceeds monthly byte budget")
            _atomic_write(path, lambda handle: handle.write(body))
    finally:
        lock.unlink(missing_ok=True)
    return {**_FLAGS, "provider": provider, "month": month, "added": added,
            "duplicate_revisions": len(prepared)-added, "input_sha256": sha256(raw).hexdigest(),
            "rights_status": "operator_attested_not_independently_verified", "network_requests": 0}


def review(provider, month, *, as_of, max_age_seconds=86400, directory=Path("local_data/news")):
    local_rights(provider, retention=True)
    cutoff = _time(as_of)
    if cutoff > datetime.now(UTC) or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 604800:
        raise ValueError("Invalid news review cutoff or freshness")
    rows = _records(_archive_path(provider, month, directory), provider, month)
    visible = {}
    for row in rows:
        observed = _time(row["observed_at"])
        if observed <= cutoff:
            key = row["metadata"]["id"]
            prior = visible.get(key)
            if prior is None or row["observed_at"] >= prior["observed_at"]:
                visible[key] = row
    events = []
    links = set()
    for row in sorted(visible.values(), key=lambda x: (x["observed_at"], x["revision_sha256"]), reverse=True):
        meta = row["metadata"]
        if meta["url"] in links:
            continue
        links.add(meta["url"])
        matches = [(kind, weight) for kind, weight, pattern in _RULES if re.search(pattern, meta["title"], re.I)]
        age = (cutoff - _time(meta["published_at"])).total_seconds()
        events.append({**meta, "observed_at": row["observed_at"], "evidence_id": row["revision_sha256"],
                       "input_sha256": row["input_sha256"], "age_seconds": age, "fresh": age <= max_age_seconds,
                       "event_categories": [x[0] for x in matches] or ["unclassified"],
                       "importance_heuristic": max((x[1] for x in matches), default=1),
                       "association_source": "vendor_supplied_unverified",
                       "sector_impact_direction": None, "directional_signal": None})
    events.sort(key=lambda e: (e["importance_heuristic"], e["published_at"], e["evidence_id"]), reverse=True)
    return {**_FLAGS, "provider": provider, "model": "headline_event_rules_v1",
            "as_of": cutoff.isoformat(), "rights_status": "operator_attested_not_independently_verified",
            "events": events, "event_counts": dict(Counter(c for e in events for c in e["event_categories"])),
            "source_coverage_verified": False, "cross_provider_combination": False,
            "importance_statistically_calibrated": False}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=sorted(_PROVIDERS))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--month")
    parser.add_argument("--as-of")
    args = parser.parse_args()
    if args.input:
        report = ingest(args.provider, args.input)
    elif args.month and args.as_of:
        report = review(args.provider, args.month, as_of=args.as_of)
        report["event_count"] = len(report.pop("events"))
    else:
        parser.error("Provide --input or --month and --as-of")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
