"""Company Facts publication and issuer identity regressions."""

import csv
from datetime import date
from gzip import open as gzopen
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atlaspm.selection.sec_fundamental_history import import_sec_factors
from atlaspm.selection.stock_screener import load_fundamental_history


class SecFundamentalHistoryTest(unittest.TestCase):
    def test_next_session_visibility_and_accession_isolation(self):
        with TemporaryDirectory() as work:
            root = Path(work)
            securities = root / "securities.csv"
            benchmark = root / "benchmark.csv"
            raw = root / "raw.csv"
            result = root / "fundamentals.csv"
            securities.write_text("symbol,cik,sector\nTEST,123,Industrials\n", encoding="utf-8")
            benchmark.write_text("date,symbol,adjusted_close\n2024-01-25,SPY,100\n2024-01-26,SPY,100\n"
                                 "2024-01-29,SPY,101\n2024-01-30,SPY,101\n", encoding="utf-8")
            raw.write_text("date,symbol,close\n2024-01-25,TEST,50\n2024-01-26,TEST,12\n2024-01-30,TEST,13\n",
                           encoding="utf-8")
            facts = {"dei": {}, "us-gaap": {}}

            def fact(taxonomy, tag, end, value, start=None, unit="USD"):
                series = facts[taxonomy].setdefault(tag, {"units": {unit: []}})["units"][unit]
                for day, accession, multiple in (("2024-01-25", "original", 1), ("2024-01-29", "amended", 2)):
                    item = {"end": end, "val": value * multiple, "accn": accession, "filed": day,
                            "form": "10-K" if accession == "original" else "10-K/A"}
                    if start is not None:
                        item["start"] = start
                    series.append(item)

            for tag, amount in {"Revenues": 1000, "NetIncomeLoss": 100, "OperatingIncomeLoss": 135,
                                "NetCashProvidedByUsedInOperatingActivities": 150,
                                "PaymentsToAcquirePropertyPlantAndEquipment": 25,
                                "DepreciationAndAmortization": 20}.items():
                fact("us-gaap", tag, "2023-12-31", amount, "2023-01-01")
            fact("us-gaap", "Revenues", "2022-12-31", 800, "2022-01-01")
            fact("us-gaap", "StockholdersEquity", "2023-12-31", 500)
            fact("us-gaap", "StockholdersEquity", "2022-12-31", 500)
            fact("us-gaap", "LongTermDebtNoncurrent", "2023-12-31", 100)
            fact("us-gaap", "CommercialPaper", "2023-12-31", 25)
            fact("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "2023-12-31", 40)
            fact("dei", "EntityCommonStockSharesOutstanding", "2024-01-22", 100, unit="shares")
            with gzopen(root / "CIK0000000123.json.gz", "wt", encoding="utf-8") as handle:
                json.dump({"cik": 123, "facts": facts}, handle)

            report = import_sec_factors(securities, root, raw, benchmark, date(2024, 1, 25), date(2024, 1, 30), result)
            self.assertEqual(report["snapshots"], 2)
            self.assertEqual(load_fundamental_history(result, date(2024, 1, 25)), {})
            latest = load_fundamental_history(result, date(2024, 1, 29))["TEST"]
            self.assertEqual(len(latest), 1)
            self.assertEqual(latest[0]["market_cap_usd"], 1200)
            self.assertEqual(latest[0]["debt_to_equity"], 0.25)
            self.assertAlmostEqual(latest[0]["fcf_yield"], 125 / 1200)
            with result.open(newline="", encoding="utf-8") as handle:
                self.assertEqual([(row["as_of_date"], row["accession"]) for row in csv.DictReader(handle)],
                                 [("2024-01-26", "original"), ("2024-01-30", "amended")])
