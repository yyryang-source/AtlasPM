"""Current US-listed equity universe from Nasdaq Trader's official daily directories.

This is a CURRENT listing snapshot, NOT a point-in-time historical 2000-2026
universe. US-listed stocks include foreign issuers/ADRs; listing != US domicile.
No trading interfaces and no implicit investor orders.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import re
from typing import Any
from urllib.request import Request, urlopen

NASDAQLISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHERLISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
_FIELDS = ("listing_symbol", "provider_symbol", "security_name", "exchange",
           "classification", "listing_source", "snapshot_utc")
_EXCHANGES = {
    "Q": "NASDAQ", "G": "NASDAQ", "S": "NASDAQ",
    "N": "NYSE", "A": "NYSE_AMERICAN", "P": "NYSE_ARCA",
    "Z": "CBOE", "V": "IEX",
}
_NON_COMMON = re.compile(
    r"\b(?:ETF|ETN|ETP|exchange.traded|preferred|preference|"
    r"warrants?|rights?|units?|debentures?|bonds?|notes?|"
    r"closed.end funds?|mutual funds?|index funds?|"
    r"depositary receipts? for preferred|subscription receipts?)\b",
    re.IGNORECASE,
)
_EQUITY = re.compile(
    r"\b(?:common stock|common shares?|ordinary shares?|american depositary"
    r" shares?|american depositary receipts?|ADS|ADR|class [A-Z] common|"
    r"shares of beneficial interest|limited partnership interests?)\b",
    re.IGNORECASE,
)
_SUPPORTED_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")


def _read_directory(url: str, *, local_path: Path | None = None) -> str:
    if local_path is not None:
        return local_path.read_text(encoding="utf-8-sig")
    request = Request(url, headers={"User-Agent": "AtlasPM-Research/0.7 (US market universe)"})
    with urlopen(request, timeout=30) as response:
        data = response.read(6_000_001)
    if len(data) > 6_000_000:
        raise ValueError(f"Listing directory exceeds 6MB: {url}")
    return data.decode("utf-8-sig")


def _parse_directory(contents: str, which: str, snapshot: str) -> tuple[list[dict[str, str]], dict[str, int | str]]:
    reader = csv.DictReader(StringIO(contents), delimiter="|")
    expected = (
        {"Symbol", "Security Name", "Test Issue", "ETF"} if which == "nasdaq"
        else {"ACT Symbol", "Security Name", "Exchange", "Test Issue", "ETF"}
    )
    if not reader.fieldnames or not expected.issubset(reader.fieldnames):
        raise ValueError(f"Incorrect {which} directory header: {reader.fieldnames}")
    records = []
    stats: dict[str, int | str] = {
        "raw_rows": 0, "test_issues": 0, "etfs": 0,
        "excluded_non_equity": 0, "invalid_symbols": 0,
        "included": 0, "unverified_equity_class": 0,
        "file_creation_time": "unknown",
    }
    for row in reader:
        if not row or all(not (x or "").strip() for x in row.values()):
            continue
        raw_symbol = (row.get("Symbol") if which == "nasdaq" else row.get("ACT Symbol")) or ""
        raw_symbol = raw_symbol.strip().upper()
        if raw_symbol.startswith("FILE CREATION TIME:"):
            stats["file_creation_time"] = raw_symbol
            continue
        stats["raw_rows"] = int(stats["raw_rows"]) + 1
        name = (row.get("Security Name") or "").strip()
        exchange_id = "Q" if which == "nasdaq" else (row.get("Exchange") or "").strip()
        if (row.get("Test Issue") or "").strip().upper() != "N":
            stats["test_issues"] = int(stats["test_issues"]) + 1
            continue
        if (row.get("ETF") or "").strip().upper() != "N":
            stats["etfs"] = int(stats["etfs"]) + 1
            continue
        if _NON_COMMON.search(name):
            stats["excluded_non_equity"] = int(stats["excluded_non_equity"]) + 1
            continue
        if (not name or not _SUPPORTED_SYMBOL.fullmatch(raw_symbol)
                or exchange_id not in _EXCHANGES):
            stats["invalid_symbols"] = int(stats["invalid_symbols"]) + 1
            continue
        classification = "name_confirmed_equity" if _EQUITY.search(name) else "unverified_equity"
        if classification == "unverified_equity":
            stats["unverified_equity_class"] = int(stats["unverified_equity_class"]) + 1
        records.append({
            "listing_symbol": raw_symbol,
            "provider_symbol": raw_symbol.replace(".", "-"),
            "security_name": name,
            "exchange": _EXCHANGES[exchange_id],
            "classification": classification,
            "listing_source": which,
            "snapshot_utc": snapshot,
        })
        stats["included"] = int(stats["included"]) + 1
    if int(stats["raw_rows"]) < 1:
        raise ValueError(f"{which} directory contains no securities")
    return records, stats


def build_us_listed_universe(
    destination: Path, *, nasdaq_file: Path | None = None,
    other_file: Path | None = None,
) -> dict[str, Any]:
    """Write a deterministic, de-duplicated CURRENT listed equity candidate CSV.

    Any security with ambiguous issue name remains included and explicitly
    flagged unverified_equity; no automated claim of perfect stock taxonomy.
    """
    snapshot = datetime.now(timezone.utc).isoformat(timespec="seconds")
    first, ns = _parse_directory(
        _read_directory(NASDAQLISTED_URL, local_path=nasdaq_file), "nasdaq", snapshot
    )
    second, os = _parse_directory(
        _read_directory(OTHERLISTED_URL, local_path=other_file), "other", snapshot
    )
    found: dict[str, dict[str, str]] = {}
    duplicates = 0
    for row in first + second:
        symbol = row["listing_symbol"]
        if symbol in found:
            duplicates += 1
            # A dual listing remains one candidate. Retain original provenance.
            continue
        found[symbol] = row
    if not found:
        raise ValueError("No qualifying listed securities")
    # Ensure the Yahoo symbol translation is one-to-one.
    mapped: dict[str, str] = {}
    collisions: list[dict[str, str]] = []
    for symbol, row in sorted(found.items()):
        provider_symbol = row["provider_symbol"]
        if provider_symbol in mapped:
            collisions.append({"symbol": symbol, "provider_symbol": provider_symbol})
        else:
            mapped[provider_symbol] = symbol
    if collisions:
        raise ValueError(f"Quote symbol mapping collision: {collisions[:5]}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerows(found[s] for s in sorted(found))
    return {
        "source": "Nasdaq Trader current-day symbol directories",
        "source_urls": [NASDAQLISTED_URL, OTHERLISTED_URL],
        "snapshot_utc": snapshot,
        "listing_count": len(found),
        "deduplicated_listing_count": duplicates,
        "unverified_issue_count": sum(
            r["classification"] == "unverified_equity" for r in found.values()
        ),
        "nasdaq_directory": ns,
        "other_directory": os,
        "output": str(destination),
        "warning": (
            "CURRENT US-listed securities, not all US-incorporated companies "
            "or a historical 2000-2026 survivorship-free universe. "
            "Issue-name classification is approximate; inspect unverified names."
        ),
        "live_execution_enabled": False,
    }


def read_market_universe(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {"listing_symbol", "provider_symbol"}.issubset(reader.fieldnames):
            raise ValueError("Universe CSV must contain listing_symbol,provider_symbol")
        rows = []
        seen: set[str] = set()
        for line, row in enumerate(reader, 2):
            s = (row.get("provider_symbol") or "").strip().upper()
            if not _SUPPORTED_SYMBOL.fullmatch(s):
                raise ValueError(f"Invalid provider symbol on line {line}")
            if s in seen:
                raise ValueError(f"Duplicate provider symbol {s}")
            seen.add(s)
            rows.append(row)
    if not rows:
        raise ValueError("Empty universe file")
    return rows
