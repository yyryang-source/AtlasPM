"""Fail-closed, source-verified reverse split reconciliation for research prices.

Never auto-infer corporate events from returns and NEVER modify raw input.
A manifest must identify an independently verified reverse split. A close
ratio is a corroborating cross-check, not a replacement for the filing.
"""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import json
from math import isfinite
import os
from pathlib import Path
import tempfile
from typing import Any


_REQUIRED_COLUMNS = {"date", "symbol", "adjusted_close"}
_EVENT_KIND = "reverse_split"
_NEAR_RATIO_TOLERANCE = 0.20


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(2 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _events(path: Path) -> list[dict[str, Any]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("events"), list):
        raise ValueError("Manifest must be a JSON object with nonempty 'events' array")
    result = []
    seen = set()
    for original in manifest["events"]:
        if not isinstance(original, dict):
            raise ValueError("Each event must be an object")
        symbol = original.get("symbol")
        day_text = original.get("effective_date")
        ratio = original.get("old_shares_per_new_share")
        source_url = original.get("source_url")
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.isupper() or not symbol.strip():
            raise ValueError("Event symbol must be a nonempty uppercase ticker")
        if original.get("type") != _EVENT_KIND:
            raise ValueError("Only reverse_split event type is supported")
        if not isinstance(day_text, str):
            raise ValueError("Event effective_date must be YYYY-MM-DD")
        try:
            event_date = date.fromisoformat(day_text)
        except ValueError as exc:
            raise ValueError("Invalid effective_date") from exc
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not isfinite(ratio) or not 1.01 <= ratio <= 10000:
            raise ValueError("old_shares_per_new_share must be finite and >1")
        if not isinstance(source_url, str) or not source_url.startswith("https://www.sec.gov/Archives/edgar/"):
            raise ValueError("An official SEC filing source URL is required")
        key = (symbol, event_date)
        if key in seen:
            raise ValueError(f"Duplicate event {symbol} {event_date}")
        seen.add(key)
        result.append({
            "symbol": symbol,
            "effective_date": day_text,
            "old_shares_per_new_share": float(ratio),
            "source_url": source_url,
            "type": _EVENT_KIND,
        })
    if not result:
        raise ValueError("At least one verified event is required")
    return result


def _observe_event_prices(
    path: Path, events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    # Index events once: millions of unrelated quote rows should not scan
    # every event or repeatedly parse an event's effective date.
    events_by_symbol: dict[str, list[tuple[date, dict[str, Any]]]] = {}
    candidates = {}
    for event in events:
        key = (event["symbol"], event["effective_date"])
        candidates[key] = {"pre": None, "post": None}
        events_by_symbol.setdefault(event["symbol"], []).append(
            (date.fromisoformat(event["effective_date"]), event)
        )
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames
        if not fields or not _REQUIRED_COLUMNS.issubset(fields):
            raise ValueError("Price CSV needs date,symbol,adjusted_close")
        for line, row in enumerate(reader, start=2):
            symbol = row["symbol"].strip().upper()
            targets = events_by_symbol.get(symbol)
            if not targets:
                continue
            try:
                d = date.fromisoformat(row["date"])
                close = float(row["adjusted_close"])
                if not isfinite(close) or close <= 0:
                    raise ValueError("Invalid adjusted close")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid event symbol price on line {line}") from exc
            for event_date, event in targets:
                record = candidates[(symbol, event["effective_date"])]
                name = "pre" if d < event_date else "post"
                old = record[name]
                if (old is None
                        or (name == "pre" and d > old[0])
                        or (name == "post" and d < old[0])):
                    record[name] = (d, close)

    diagnostics = []
    for event in events:
        observation = candidates[(event["symbol"], event["effective_date"])]
        pre, post = observation["pre"], observation["post"]
        detail = dict(event)
        if pre is None or post is None:
            detail.update({
                "status": "INSUFFICIENT_DATA", "last_pre_event_date": None if pre is None else pre[0].isoformat(),
                "first_post_event_date": None if post is None else post[0].isoformat(),
                "raw_close_multiplier": None,
                "normalized_close_return_if_split": None,
            })
        else:
            multiplier = post[1] / pre[1]
            ratio = event["old_shares_per_new_share"]
            if abs(multiplier / ratio - 1) <= _NEAR_RATIO_TOLERANCE:
                status = "LIKELY_MISSING_ADJUSTMENT"
            elif abs(multiplier - 1) <= _NEAR_RATIO_TOLERANCE:
                status = "ALREADY_ADJUSTED_OR_NORMAL_PRICE"
            else:
                status = "AMBIGUOUS_NEEDS_INDEPENDENT_REVIEW"
            detail.update({
                "status": status,
                "last_pre_event_date": pre[0].isoformat(),
                "last_pre_event_close": pre[1],
                "first_post_event_date": post[0].isoformat(),
                "first_post_event_close": post[1],
                "raw_close_multiplier": multiplier,
                "raw_close_return": multiplier - 1,
                "normalized_close_return_if_split": post[1] / (pre[1] * ratio) - 1,
            })
        diagnostics.append(detail)
    return diagnostics, fields


def reconcile_reverse_splits(
    prices_path: Path,
    manifest_path: Path,
    *,
    output_path: Path | None = None,
    apply_confirmed: bool = False,
) -> dict[str, Any]:
    """Audit source-verified events, optionally write a distinct corrected CSV.

    A correction is permitted only when the daily close move matches
    the filing's reverse-split ratio to within 20% and all events can
    be classified. This tolerance is a safety guard, not proof.
    Existing already-adjusted splits are not multiplied a second time.
    """
    prices_path = Path(prices_path).expanduser().resolve()
    manifest_path = Path(manifest_path).expanduser().resolve()
    if not prices_path.is_file() or not manifest_path.is_file():
        raise ValueError("Both prices CSV and split manifest must exist")
    if apply_confirmed and output_path is None:
        raise ValueError("--apply-confirmed requires --output research CSV")
    if not apply_confirmed and output_path is not None:
        raise ValueError("--output is only valid with --apply-confirmed")
    destination = Path(output_path).expanduser().resolve() if output_path else None
    if destination and (destination in {prices_path, manifest_path} or destination.exists()):
        raise ValueError("Corrected output must be a NEW distinct file, never overwrite raw data")
    events = _events(manifest_path)
    checks, fields = _observe_event_prices(prices_path, events)

    corrective = [event for event in checks if event["status"] == "LIKELY_MISSING_ADJUSTMENT"]
    uncertain = [event for event in checks if event["status"] in {
        "INSUFFICIENT_DATA", "AMBIGUOUS_NEEDS_INDEPENDENT_REVIEW",
    }]
    if apply_confirmed and uncertain:
        raise ValueError("Refusing corrections: unresolved events require independent review")

    result = {
        "status": "REVIEW_REQUIRED" if corrective or uncertain else "NO_CORRECTION_INDICATED",
        "research_only": True,
        "live_execution_enabled": False,
        "raw_prices_path": str(prices_path),
        "raw_sha256": _sha256(prices_path),
        "source_manifest": str(manifest_path),
        "event_checks": checks,
        "events_requiring_reconciliation": len(corrective),
        "events_requiring_manual_review": len(uncertain),
        "corrected_prices_path": None,
        "corrected_sha256": None,
        "warnings": [
            "A split-adjusted research copy is NOT point-in-time listing membership or a reliable tradability/volume test.",
            "Never delete original data or manually drop negative-return names to manufacture alpha.",
            "The SEC source is independent event evidence; this process does not independently verify Yahoo historical prices.",
            "Price-gap classification is heuristic, requires human review, and cannot establish model profitability.",
        ],
    }
    if not apply_confirmed:
        return result

    # Build split-normalization factors across all *preceding* dates. This
    # preserves comparable share units for features and labels crossing events.
    factors_by_symbol: dict[str, list[tuple[date, float]]] = {}
    for event in corrective:
        factors_by_symbol.setdefault(event["symbol"], []).append(
            (date.fromisoformat(event["effective_date"]),
             event["old_shares_per_new_share"])
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = None
    modified = 0
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=destination.parent,
            prefix=".atlaspm_split_reconcile_", suffix=".tmp", delete=False,
        ) as tmp:
            tmp_file = Path(tmp.name)
            with prices_path.open("r", encoding="utf-8-sig", newline="") as source:
                reader = csv.DictReader(source)
                if reader.fieldnames != fields:
                    raise ValueError("CSV header changed during reconciliation")
                writer = csv.DictWriter(tmp, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                for row in reader:
                    day = date.fromisoformat(row["date"])
                    symbol = row["symbol"].strip().upper()
                    factor = 1.0
                    for effective, multiple in factors_by_symbol.get(symbol, ()):
                        if day < effective:
                            factor *= multiple
                    if factor != 1.0:
                        close = float(row["adjusted_close"])
                        adjusted = close * factor
                        if not isfinite(adjusted) or adjusted <= 0:
                            raise ValueError(f"Unusable corrected price for {symbol} {day}")
                        row["adjusted_close"] = format(adjusted, ".15g")
                        modified += 1
                    writer.writerow(row)
        if destination.exists():
            raise ValueError("Destination appeared during reconciliation: refusing overwrite")
        # Protect inputs; temporary files are on the same filesystem.
        # os.rename refuses overwriting on Windows; do not rename existing targets.
        os.rename(tmp_file, destination)
        tmp_file = None
        result["status"] = "CORRECTED_RESEARCH_COPY_NEEDS_REAUDIT"
        result["corrected_prices_path"] = str(destination)
        result["corrected_sha256"] = _sha256(destination)
        result["modified_rows"] = modified
    finally:
        if tmp_file is not None:
            tmp_file.unlink(missing_ok=True)
    return result
