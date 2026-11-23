"""Normalize LEAN US minute option quote files without inferring contract terms."""

from __future__ import annotations

import csv
from datetime import datetime, time, timedelta
from io import TextIOWrapper
from pathlib import Path
import re
from shutil import copyfileobj
from tempfile import TemporaryFile
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

from atlaspm.quant.option_history import load_option_contracts


_MEMBER = re.compile(r"(\d{8})_([^_]+)_minute_quote_(american|european)_(call|put)_(\d+)_(\d{8})\.csv")


def import_lean_option_quotes(archives: list[Path], contract_path: Path, output: Path) -> dict[str, int]:
    zone = ZoneInfo("America/New_York")
    contracts = load_option_contracts(contract_path)
    by_terms = {}
    for identifier, contract in contracts.items():
        key = (contract.underlying, contract.exercise_style, contract.right, contract.strike,
               contract.expiration_at.astimezone(zone).date())
        if key in by_terms:
            raise ValueError("LEAN filenames cannot distinguish input contracts with identical filename terms")
        by_terms[key] = identifier
    seen = set()
    excluded = skipped_members = 0

    with TemporaryFile(mode="w+", encoding="utf-8", newline="") as staged:
        writer = csv.writer(staged)
        writer.writerow(["contract_id", "quote_at", "bid", "ask", "bid_size", "ask_size"])
        for path in archives:
            try:
                with ZipFile(path) as archive:
                    for member in archive.infolist():
                        if member.is_dir():
                            continue
                        match = _MEMBER.fullmatch(Path(member.filename).name)
                        if match is None:
                            raise ValueError(f"Expected a LEAN US minute option quote member: {member.filename}")
                        observed, symbol, style, right, strike, expiry = match.groups()
                        expiration_date = datetime.strptime(expiry, "%Y%m%d").date()
                        identifier = by_terms.get((symbol.upper(), style, right, int(strike) / 10000, expiration_date))
                        if identifier is None:
                            skipped_members += 1
                            continue
                        session = datetime.strptime(observed, "%Y%m%d").date()
                        midnight = datetime.combine(session, time(), tzinfo=zone)
                        with archive.open(member) as handle:
                            reader = csv.reader(TextIOWrapper(handle, encoding="utf-8-sig", newline=""))
                            for number, row in enumerate(reader, 1):
                                if len(row) != 11:
                                    raise ValueError(f"Invalid LEAN minute quote width in {member.filename}, row {number}")
                                millis = int(row[0])
                                bid, ask = int(row[4]), int(row[9])
                                bid_size, ask_size = int(row[5]), int(row[10])
                                if not 0 <= millis < 86_400_000 or min(bid_size, ask_size) < 0:
                                    raise ValueError(f"Invalid LEAN quote time or size in {member.filename}, row {number}")
                                at = midnight + timedelta(milliseconds=millis, minutes=1)
                                if not 0 <= bid <= ask or ask == 0 or at > contracts[identifier].last_trade_at:
                                    excluded += 1
                                    continue
                                key = (identifier, at)
                                if key in seen:
                                    raise ValueError(f"Duplicate LEAN quote for {identifier} at {at.isoformat()}")
                                seen.add(key)
                                writer.writerow([identifier, at.isoformat(), bid / 10000, ask / 10000, bid_size, ask_size])
            except BadZipFile as exc:
                raise ValueError(f"Invalid LEAN option ZIP: {path.name}") from exc
        if not seen:
            raise ValueError("No compatible LEAN quote rows for the supplied contracts")
        output.parent.mkdir(parents=True, exist_ok=True)
        staged.seek(0)
        with output.open("x", encoding="utf-8", newline="") as handle:
            copyfileobj(staged, handle)
    return {"quotes_written": len(seen), "excluded_quotes": excluded, "members_without_terms": skipped_members}
