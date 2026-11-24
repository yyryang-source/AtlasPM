"""Normalize the public HistoricalData.net SPY option sample for local research."""

from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal
from hashlib import file_digest, sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pandas_market_calendars as calendars


SOURCE = "https://historicaldata.net/options.html"
DIVIDEND_SOURCE = "https://www.ssga.com/au/en_gb/institutional/etfs/state-street-spdr-sp-500-etf-spy"
DIVIDENDS = {"2022-09-16": "1.59640", "2022-12-16": "1.78140"}


def _write_inputs(archive: Path, destination: Path) -> None:
    expected_first = date(2022, 7, 1)
    expected_last = date(2022, 12, 30)
    schedule = calendars.get_calendar("NYSE").schedule(expected_first, expected_last + timedelta(days=66))
    session_dates = [day.date() for day in schedule.index]
    closes = {day: (stamp + timedelta(minutes=15)).to_pydatetime()
              for day, stamp in zip(session_dates, schedule["market_close"], strict=True)}
    opens_after = {day: stamp.to_pydatetime()
                   for day, stamp in zip(session_dates[:-1], schedule["market_open"].iloc[1:], strict=True)}
    contracts = {}
    valid_quotes = missing_quotes = 0
    daily_coverage = {}
    with (
        ZipFile(archive) as incoming,
        (destination / "quotes.csv").open("x", newline="", encoding="utf-8") as quotes,
        (destination / "stock_marks.csv").open("x", newline="", encoding="utf-8") as stock,
    ):
        manifest = json.loads(incoming.read("day_by_date/manifest.json"))
        files = {row["name"]: row for row in manifest["files"]}
        sample_sessions = [day for day in session_dates if day <= expected_last]
        if set(files) != {f"{session}_options.csv" for session in sample_sessions}:
            raise ValueError("Archive files disagree with the expected July–December 2022 NYSE sessions")
        quote_writer = csv.writer(quotes)
        quote_writer.writerow(["contract_id", "quote_at", "bid", "ask", "bid_size", "ask_size"])
        stock_writer = csv.writer(stock)
        stock_writer.writerow(["observed_at", "close", "sale_cash_at", "dividend_per_share"])
        for session in sample_sessions:
            name = f"{session}_options.csv"
            raw = incoming.read(f"day_by_date/{name}")
            if sha256(raw).hexdigest() != files[name]["sha256"] or len(raw) != files[name]["bytes"]:
                raise ValueError(f"Sample file checksum mismatch for {name}")
            stock_prices = set()
            before = valid_quotes
            snapshot = closes[session].isoformat()
            for row in csv.DictReader(StringIO(raw.decode("utf-8-sig"), newline="")):
                if row["underlying"] != "SPY":
                    continue
                if row["quote_date"] != str(session) or row["quote_time"]:
                    raise ValueError(f"Expected untimed EOD observations in {name}")
                stock_prices.add(row["underlying_close"])
                if row["type"] != "put" or row["style"] != "A" or row["settlement_time"] != "PM":
                    continue
                expiry = date.fromisoformat(row["expiration"])
                if not 0 < (expiry - session).days <= 65 or expiry not in closes:
                    continue
                strike = Decimal(row["strike"])
                key = row["contract"]
                if key != f"SPY{expiry:%y%m%d}P{int(strike * 1000):08d}":
                    continue
                if (not all(row[column] for column in ("bid", "ask", "bid_size", "ask_size"))
                        or Decimal(row["bid"]) > Decimal(row["ask"])):
                    missing_quotes += 1
                    continue
                last_trade = closes[expiry]
                contracts[key] = [key, "SPY", "put", strike, last_trade.isoformat(),
                                  (last_trade + timedelta(hours=2)).isoformat(), 100, "american", "physical", 1]
                quote_writer.writerow([key, snapshot, row["bid"], row["ask"], row["bid_size"], row["ask_size"]])
                valid_quotes += 1
            if len(stock_prices) != 1 or "" in stock_prices or valid_quotes == before:
                raise ValueError(f"Insufficient or inconsistent SPY observations for {session}")
            stock_writer.writerow([snapshot, stock_prices.pop(), opens_after[session].isoformat(),
                                   DIVIDENDS.get(str(session), "0")])
            daily_coverage[str(session)] = valid_quotes - before
    with (destination / "contracts.csv").open("x", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["contract_id", "underlying", "right", "strike", "last_trade_at", "expiration_at",
                         "multiplier", "exercise_style", "settlement_type", "exercise_threshold_usd"])
        writer.writerows(contracts[key] for key in sorted(contracts))
    (destination / "settlements.csv").write_text("contract_id,reference_at,available_at,cash_at,settlement_value\n",
                                                  encoding="utf-8")
    hashes = {}
    for path in (archive, *sorted(destination.glob("*.csv"))):
        with path.open("rb") as handle:
            hashes[path.name] = file_digest(handle, "sha256").hexdigest()
    report = {"source": SOURCE, "dividend_source": DIVIDEND_SOURCE, "sha256": hashes,
              "sessions": len(daily_coverage), "options_with_quotes": len(contracts), "valid_quotes": valid_quotes,
              "missing_or_crossed_quotes": missing_quotes, "quotes_by_session": daily_coverage,
              "timing_assumptions": "Untimed EOD quotes at NYSE close plus 15 minutes. Sale cash at the next NYSE open. "
              "Expiration cutoff is a planning input for pre-expiry exit; no expiration settlement is supplied."}
    (destination / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def prepare(archive: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temp:
        staged = Path(temp) / "normalized"
        staged.mkdir()
        _write_inputs(archive, staged)
        staged.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    prepare(args.archive, args.destination)
