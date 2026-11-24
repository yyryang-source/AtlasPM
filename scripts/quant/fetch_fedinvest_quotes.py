"""Download TreasuryDirect date-specific EOD prices and normalize a single CUSIP."""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
from decimal import Decimal
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from io import StringIO
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, build_opener

from atlaspm.execution.ibkr_gateway import require_private_output


_BASE = "https://www.treasurydirect.gov/GA-FI/FedInvest/"


class _Forms(HTMLParser):
    def __init__(self, raw: bytes):
        super().__init__()
        self.fields: dict[str, dict[str, str]] = {}
        self.current: str | None = None
        self.feed(raw.decode("utf-8"))

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.current = attributes.get("id")
            if self.current:
                self.fields[self.current] = {}
        if tag == "input" and self.current and attributes.get("name") and "value" in attributes:
            self.fields[self.current][attributes["name"]] = attributes["value"]

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class _FedInvest:
    def __init__(self):
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.start_page = self._request("selectSecurityPriceDate")

    def _request(self, endpoint: str, parameters: dict[str, str] | None = None) -> bytes:
        body = None if parameters is None else urlencode(parameters).encode("ascii")
        with self.opener.open(_BASE + endpoint, data=body, timeout=20) as response:
            return response.read()

    def prices(self, day: date) -> bytes:
        fields = _Forms(self.start_page).fields["fromToDate"]
        export_page = self._request("selectSecurityPriceDate", fields | {"priceDate": str(day), "submit": "Show Prices"})
        download = _Forms(export_page).fields["CSVFormat"]
        returned = date(*(int(download[f"priceDate{name}"]) for name in ("Year", "Month", "Day")))
        if returned != day:
            raise ValueError(f"TreasuryDirect returned {returned} for requested date {day}")
        return self._request("securityPriceDetail", download)


def fetch_quotes(contracts: Path, cusip: str, days: list[date], output: Path, raw_dir: Path) -> dict:
    with contracts.open(encoding="utf-8-sig", newline="") as handle:
        matches = [row for row in csv.DictReader(handle) if row["cusip"].strip().upper() == cusip]
    if len(matches) != 1:
        raise ValueError(f"Expected one contract row for {cusip}")
    terms = matches[0]
    coupon = Decimal(terms["annual_coupon_rate"])
    maturity = date.fromisoformat(terms["maturity"])
    issued = date.fromisoformat(terms["issued_on"])
    expected_kind = f'MARKET BASED {terms["kind"].strip().upper()}'
    if (not days or len(days) != len(set(days)) or min(days) < issued or max(days) >= maturity
            or any(day.weekday() >= 5 for day in days)):
        raise ValueError("Use unique Treasury pricing weekdays from issue through the day before maturity")
    if output.exists():
        raise FileExistsError(output)
    raw_dir.mkdir(parents=True, exist_ok=True)
    service = None
    quotes = []
    missing = []
    for day in sorted(days):
        path = raw_dir / f"fedinvest_{day}.csv"
        cached = path.exists()
        if cached:
            raw = path.read_bytes()
        else:
            if service is None:
                service = _FedInvest()
            raw = service.prices(day)
        prices = list(csv.reader(StringIO(raw.decode("utf-8-sig"))))
        if not prices or any(len(row) != 8 for row in prices):
            raise ValueError(f"Invalid TreasuryDirect price export for {day}")
        rows = [row for row in prices if row[0] == cusip]
        if len(rows) > 1:
            raise ValueError(f"Duplicate source rows for {cusip} on {day}")
        if rows:
            _, kind, rate, mat, _, _, _, end_of_day = rows[0]
            if (kind != expected_kind or Decimal(rate) != coupon
                    or datetime.strptime(mat, "%m/%d/%Y").date() != maturity):
                raise ValueError(f"TreasuryDirect contract terms disagree for {cusip} on {day}")
            if end_of_day and Decimal(end_of_day) > 0:
                quotes.append((cusip, day, end_of_day))
            else:
                missing.append(str(day))
        else:
            missing.append(str(day))
        if not cached:
            with path.open("xb") as handle:
                handle.write(raw)
    if not quotes:
        raise ValueError(f"No TreasuryDirect end-of-day price for {cusip} on the requested dates")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cusip", "observed_on", "clean_close_per_100"])
        writer.writerows(quotes)
    return {"rows": len(quotes), "missing_dates": missing, "cusip": cusip}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", required=True, type=Path)
    parser.add_argument("--cusip", required=True)
    parser.add_argument("--dates", required=True, nargs="+", type=date.fromisoformat)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    args = parser.parse_args()
    result = fetch_quotes(args.contracts, args.cusip.strip().upper(), args.dates,
                          require_private_output(args.output), require_private_output(args.raw_dir))
    print(json.dumps(result, indent=2))
