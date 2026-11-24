"""V5 evidence contract and point-in-time regression tests, synthetic only."""
from datetime import date
from decimal import Decimal
import csv
import os
from pathlib import Path
import tempfile
import unittest

from atlaspm.data import hf_stock5m_certification_v5 as v5
from atlaspm.data import hf_stock5m as hf


class CertificationV5Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prev = Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir, self.prev)
        Path("local_data").mkdir()
        self.ids = Path("local_data/identities.csv")
        self.acts = Path("local_data/actions.csv")
        self.rows = [
            ("OLD", "ABC", "XNYS", "2000-01-01", "2010-01-01", "1999-12-31", "fixture", "id1"),
            ("NEW", "ABC", "XNAS", "2020-01-01", "", "2019-12-31", "fixture", "id2"),
            ("LATE", "XYZ", "XNYS", "2025-11-01", "", "2025-11-28", "fixture", "id3"),
        ]
        self.write(self.ids, v5.IDENTITY_FIELDS, self.rows)
        self.write(self.acts, v5.ACTION_FIELDS, [
            ("S1", "NEW", "SPLIT", "2024-01-02", "2023-12-30", "4", "", "fixture", "a1"),
            ("D1", "NEW", "CASH_DIVIDEND", "2025-02-01", "2025-01-01", "0.25", "USD", "fixture", "a2"),
        ])

    def write(self, path, header, rows):
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)

    def test_ticker_reuse_and_exclusive_delisting(self):
        ids = v5.load_identities(self.ids)
        d = date.fromisoformat
        self.assertEqual(v5.resolve(ids,"ABC",d("2009-12-31"),d("2009-12-31")).security_id,"OLD")
        self.assertIsNone(v5.resolve(ids,"ABC",d("2010-01-01"),d("2010-01-01")))
        self.assertEqual(v5.resolve(ids,"ABC",d("2025-11-28"),d("2025-11-28")).security_id,"NEW")
        self.assertIsNone(v5.resolve(ids,"XYZ",d("2025-11-28"),d("2025-11-28")))

    def test_overlap_rejected(self):
        self.write(self.ids,v5.IDENTITY_FIELDS,self.rows+[
            ("OTHER","ABC","XNYS","2005-01-01","2006-01-01","2004-12-31","fixture","id4")])
        with self.assertRaisesRegex(ValueError,"Overlapping symbol"):
            v5.load_identities(self.ids)

    def test_split_adjustment_requires_unadjusted_and_no_future(self):
        actions = v5.load_actions(self.acts,v5.load_identities(self.ids))
        d = date.fromisoformat
        with self.assertRaises(ValueError):
            v5.split_adjustment(actions,"NEW",d("2023-01-01"),d("2025-01-01"))
        self.assertEqual(v5.split_adjustment(actions,"NEW",d("2023-01-01"),d("2025-01-01"),raw_unadjusted=True),
                         (Decimal("0.25"),Decimal("4")))
        self.assertEqual(v5.split_adjustment(actions,"NEW",d("2023-01-01"),d("2023-12-31"),raw_unadjusted=True),
                         (Decimal(1),Decimal(1)))

    def test_unknown_or_duplicate_actions_rejected(self):
        self.write(self.acts,v5.ACTION_FIELDS,[
            ("S1","MISSING","SPLIT","2024-01-02","2023-12-30","2","","fixture","a1")])
        with self.assertRaises(ValueError):
            v5.load_actions(self.acts,v5.load_identities(self.ids))

    def test_missing_and_present_evidence_do_not_authorize(self):
        r = v5.run()
        self.assertIn("MISSING_HISTORICAL_IDENTITY_LEDGER",r["blockers"])
        r = v5.run("2025-11","2025-11",identity_csv=self.ids,actions_csv=self.acts)
        self.assertEqual(r["evidence"]["identities"]["rows"],3)
        self.assertEqual(r["evidence"]["corporate_actions"]["split_events"],1)
        self.assertEqual(len(r["evidence"]["identities"]["sha256"]),64)
        self.assertFalse(r["approved_for_model_training"])
        self.assertFalse(r["live_execution_enabled"])
        self.assertEqual(r["months_scanned"],0)

    def test_observed_mapping_scan(self):
        try:
            import duckdb
            import pandas_market_calendars
        except ImportError:
            self.skipTest("Install lake/calendar extras")
        path = hf.output_path("2025-11")
        path.parent.mkdir(parents=True)
        cx = duckdb.connect()
        try:
            cx.execute("""CREATE TABLE bars AS SELECT symbol,
                DATE '2025-11-28' AS trade_date_et,
                epoch_ms(TIMESTAMPTZ '2025-11-28 14:30:00+00') AS timestamp_ms
                FROM (VALUES ('ABC'),('XYZ'),('MISSING')) AS t(symbol)""")
            cx.execute(f"COPY bars TO '{hf._quote(path)}' (FORMAT PARQUET)")
        finally:
            cx.close()
        r = v5.run("2025-11","2025-11",identity_csv=self.ids,actions_csv=self.acts,scan=True)
        self.assertEqual(r["months_scanned"],1)
        self.assertEqual(r["months"][0]["mapping_counts"],{
            "MAPPED":{"symbol_days":1,"bars":1},
            "NOT_KNOWN_BEFORE_SESSION":{"symbol_days":1,"bars":1},
            "NO_EFFECTIVE_IDENTITY":{"symbol_days":1,"bars":1}})
        self.assertIn("IDENTITY_MAPPING_GAPS_OR_SCAN_ERRORS",r["blockers"])
        self.assertFalse(r["approved_for_model_training"])
