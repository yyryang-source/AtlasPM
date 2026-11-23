"""Offline synthetic HF-to-daily reconciliation regression tests."""
from __future__ import annotations

import csv
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m, hf_stock5m_reconcile

try:
    import duckdb
    import pandas_market_calendars
except ImportError:
    duckdb = None


@unittest.skipUnless(duckdb is not None, "Requires optional lake/calendar packages")
class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.prev=Path.cwd()
        os.chdir(self.temp.name)
        Path("local_data").mkdir()
        self.source=hf_stock5m.output_path("2026-03")
        self.source.parent.mkdir(parents=True,exist_ok=True)
        self.reference=Path("local_data/ref.csv")
    def tearDown(self):
        os.chdir(self.prev)
        self.temp.cleanup()

    def seed(self, *, close=105, missing_end=False):
        cx=duckdb.connect()
        last = "20:50:00" if missing_end else "20:55:00"
        cx.execute(f"""
            CREATE TABLE rows AS
            SELECT 'SPY'::VARCHAR AS symbol, DATE '2026-03-02' AS trade_date_et,
                   epoch_ms(TIMESTAMPTZ '2026-03-02 14:30:00+00') AS timestamp_ms,
                   100.0::DOUBLE AS open, 101.0::DOUBLE AS high,
                   99.0::DOUBLE AS low, 100.5::DOUBLE AS close,
                   400.0::DOUBLE AS volume_shares, NULL::BIGINT AS transactions,
                   5::BIGINT AS source_minute_bars,5::BIGINT AS unique_source_minutes
            UNION ALL
            SELECT 'SPY',DATE '2026-03-02',
                   epoch_ms(TIMESTAMPTZ '2026-03-02 {last}+00'),
                   103,106,102,{float(close)},700,NULL,5,5
        """)
        cx.execute(f"COPY rows TO '{hf_stock5m._quote(self.source)}' (FORMAT PARQUET)")
        cx.close()

    def write(self, rows):
        with self.reference.open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=["date","symbol","close","volume"])
            writer.writeheader()
            writer.writerows(rows)

    def test_matching_reference_still_not_training_approved(self):
        self.seed()
        self.write([{"date":"2026-03-02","symbol":"SPY","close":105,"volume":1100}])
        with patch.object(hf_stock5m_reconcile,"_from_yahoo",
                          side_effect=AssertionError("Unexpected network")):
            report=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                                  reference_csv=self.reference)
        self.assertEqual(1,report["compared_symbol_days"])
        self.assertEqual([],report["review_reasons"])
        self.assertTrue(report["reference_prices_reconciled"])
        self.assertFalse(report["approved_for_model_training"])
        self.assertTrue(Path(report["report_path"]).is_file())
        self.assertEqual(0,report["per_symbol"][0]["close_outliers"])

    def test_detects_large_price_ratio_without_adjusting(self):
        self.seed(close=105)
        self.write([{"date":"2026-03-02","symbol":"SPY","close":21,"volume":1100}])
        report=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                              reference_csv=self.reference,save=False)
        self.assertIn("REFERENCE_CLOSE_DIVERGENCE",report["review_reasons"])
        self.assertEqual(1,report["per_symbol"][0]["close_outliers"])
        self.assertEqual(5,report["close_outlier_examples"][0]["hf_reference_price_ratio"])
        self.assertEqual("REVIEW_REQUIRED",report["quality_status"])

    def test_skips_incomplete_open_or_close_edge(self):
        self.seed(missing_end=True)
        self.write([{"date":"2026-03-02","symbol":"SPY","close":105,"volume":1100}])
        report=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                              reference_csv=self.reference,save=False)
        self.assertEqual(0,report["compared_symbol_days"])
        self.assertIn("INCOMPLETE_DAY_START_OR_END",report["review_reasons"])
        self.assertIn("NO_COMPARABLE_DAYS",report["review_reasons"])

    def test_duplicate_reference_days_fail_closed(self):
        self.seed()
        record={"date":"2026-03-02","symbol":"SPY","close":105,"volume":1100}
        self.write([record,record])
        with self.assertRaisesRegex(ValueError,"Duplicate reference"):
            hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                           reference_csv=self.reference)

    def test_no_implicit_network(self):
        self.seed()
        with patch.object(hf_stock5m_reconcile,"_from_yahoo",
                          side_effect=AssertionError("Unexpected network")):
            with self.assertRaisesRegex(ValueError,"Supply exactly one"):
                hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"])

    def test_unmatched_reference_month_is_not_silently_accepted(self):
        self.seed()
        self.write([{"date":"2026-03-03","symbol":"SPY","close":105,"volume":1100}])
        report=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                              reference_csv=self.reference,save=False)
        self.assertIn("UNMATCHED_REFERENCE_DATES",report["review_reasons"])
        self.assertEqual(0,report["compared_symbol_days"])


if __name__=="__main__":
    unittest.main()
