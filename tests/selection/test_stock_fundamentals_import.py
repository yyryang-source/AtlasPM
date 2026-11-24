"""Check publication timing and compatibility with the existing screener."""

import contextlib
import csv
from datetime import date
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atlaspm.cli import main
from atlaspm.strategies.equity.stock_screener import load_fundamental_history


class StockFundamentalImportTest(unittest.TestCase):
    def test_import_uses_next_known_session_and_preserves_original_and_amended_values(self):
        filings = [
            ("A", "2024-02-17T02:30:00Z", "2024-02-20", 2023, "original", 18),
            ("A", "2024-02-22T21:01:00Z", "2024-02-22", 2022, "older-amendment", 80),
            ("A", "2024-03-01T21:30:00Z", "2024-03-01", 2023, "current-amendment", 27),
            ("B", "2024-03-01T21:30:00Z", "2024-03-01", 2023, "missing-valuation", 18),
        ]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (root / "filings.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "symbol", "sector", "accepted_at", "filed_date", "period_start", "period_end",
                    "prior_period_end", "source_ref", "revenue_usd", "prior_revenue_usd", "net_income_usd",
                    "equity_usd", "prior_equity_usd", "debt_usd", "operating_cash_flow_usd", "capex_usd",
                    "operating_income_usd", "depreciation_amortization_usd", "cash_usd",
                ])
                for symbol, accepted, filed, year, ref, income in filings:
                    writer.writerow([symbol, "Technology", accepted, filed, f"{year}-01-01", f"{year}-12-31",
                                     f"{year-1}-12-31", ref, 120, 100, income, 100, 80, 30, 25, 5, 25, 7, 10])
            (root / "valuations.csv").write_text(
                "date,symbol,market_cap_usd,source_ref\n2024-02-20,A,200,close-original\n"
                "2024-03-04,A,250,close-amendment\n2024-03-05,B,100,future-only\n", encoding="utf-8",
            )
            (root / "benchmark.csv").write_text(
                "date,symbol,adjusted_close\n2024-02-16,SPY,100\n2024-02-20,SPY,101\n2024-02-22,SPY,102\n"
                "2024-02-23,SPY,103\n2024-03-01,SPY,104\n2024-03-04,SPY,105\n2024-03-05,SPY,106\n",
                encoding="utf-8",
            )
            args = ["stock-import-fundamentals", "--filings", "filings.csv", "--market-caps", "valuations.csv",
                    "--benchmark-prices", "benchmark.csv"]
            cwd = Path.cwd()
            try:
                os.chdir(root)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(main(args), 0)
                summary = json.loads(out.getvalue())
                exported = Path(summary["written"])
                self.assertEqual(summary["filing_count"], 4)
                self.assertEqual(summary["snapshot_count"], 2)
                self.assertEqual(summary["skipped"], {"MISSING_PUBLICATION_SESSION_MARKET_CAP": 1,
                                                      "OLDER_PERIOD_AMENDMENT": 1})
                self.assertEqual(load_fundamental_history(exported, date(2024, 2, 19)), {})
                snapshots = load_fundamental_history(exported, date(2024, 3, 4))["A"]
                self.assertEqual([row["as_of_date"] for row in snapshots], [date(2024, 2, 20), date(2024, 3, 4)])
                self.assertEqual([row["roe"] for row in snapshots], [.2, .3])
                self.assertAlmostEqual(snapshots[0]["fcf_yield"], .1)
                self.assertAlmostEqual(snapshots[1]["ev_to_ebitda"], (250 + 30 - 10) / 32)
                with exported.open(encoding="utf-8", newline="") as handle:
                    records = list(csv.DictReader(handle))
                self.assertEqual(records[0]["filing_source_ref"], "original")
                self.assertEqual(records[1]["market_cap_source_ref"], "close-amendment")
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(args), 2)
            finally:
                os.chdir(cwd)
