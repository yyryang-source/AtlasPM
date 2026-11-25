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

    def seed(self, *, close=105, missing_end=False, missing_middle=False, mutation=None):
        cx=duckdb.connect()
        count=77 if missing_end else 78
        where="WHERE i <> 15" if missing_middle else ""
        cx.execute(f"""
            CREATE TABLE rows AS
            SELECT 'SPY'::VARCHAR AS symbol,
                   DATE '2026-03-02' AS trade_date_et,
                   epoch_ms(TIMESTAMPTZ '2026-03-02 14:30:00+00' +
                            i * INTERVAL '5 minutes') AS timestamp_ms,
                   100.0::DOUBLE AS open,
                   106.0::DOUBLE AS high,
                   99.0::DOUBLE AS low,
                   (CASE WHEN i=77 THEN {float(close)} ELSE 100.5 END)::DOUBLE AS close,
                   10.0::DOUBLE AS volume_shares,
                   NULL::BIGINT AS transactions,
                   5::BIGINT AS source_minute_bars,
                   5::BIGINT AS unique_source_minutes
            FROM range({count}) t(i) {where}
        """)
        if mutation:
            cx.execute(mutation)
        cx.execute(f"COPY rows TO '{hf_stock5m._quote(self.source)}' (FORMAT PARQUET)")
        cx.close()

    def write(self, rows):
        with self.reference.open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=["date","symbol","close","volume"])
            writer.writeheader()
            writer.writerows(rows)

    def test_matching_reference_still_not_training_approved(self):
        self.seed()
        self.write([{"date":"2026-03-02","symbol":"SPY","close":105,"volume":780}])
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
        self.write([{"date":"2026-03-02","symbol":"SPY","close":21,"volume":780}])
        report=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                              reference_csv=self.reference,save=False)
        self.assertIn("REFERENCE_CLOSE_DIVERGENCE",report["review_reasons"])
        self.assertEqual(1,report["per_symbol"][0]["close_outliers"])
        self.assertEqual(5,report["close_outlier_examples"][0]["hf_reference_price_ratio"])
        self.assertEqual("REVIEW_REQUIRED",report["quality_status"])

    def test_skips_incomplete_open_or_close_edge(self):
        self.seed(missing_end=True)
        self.write([{"date":"2026-03-02","symbol":"SPY","close":105,"volume":780}])
        report=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                              reference_csv=self.reference,save=False)
        self.assertEqual(0,report["compared_symbol_days"])
        self.assertIn("INCOMPLETE_DAY_START_OR_END",report["review_reasons"])
        self.assertIn("NO_COMPARABLE_DAYS",report["review_reasons"])

    def test_partial_internal_minutes_skip_volume_not_close(self):
        self.seed(missing_middle=True)
        self.write([{"date":"2026-03-02","symbol":"SPY","close":105,"volume":780}])
        report=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                              reference_csv=self.reference,save=False)
        self.assertEqual(1,report["compared_symbol_days"])
        self.assertEqual(1,report["per_symbol"][0]["incomplete_rth_volume_days"])
        self.assertIn("PARTIAL_RTH_VOLUME_NOT_COMPARABLE",report["review_reasons"])
        self.assertEqual(0,report["per_symbol"][0]["volume_outliers"])

    def test_missing_requested_symbol_blocks_otherwise_matching_sample(self):
        self.seed()
        self.write([{"date":"2026-03-02","symbol":"SPY","close":105,"volume":780}])
        r=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY","QQQ"],
                                        reference_csv=self.reference,save=False)
        self.assertEqual(r["compared_symbol_days"],1)
        self.assertIn("REQUESTED_SYMBOL_WITHOUT_COMPARABLE_DAYS",r["review_reasons"])
        self.assertFalse(r["reference_prices_reconciled"])

    def test_duplicate_bucket_masks_missing_bucket(self):
        self.seed(mutation="UPDATE rows SET timestamp_ms=timestamp_ms-300000 WHERE timestamp_ms=(SELECT min(timestamp_ms)+600000 FROM rows)")
        self.write([{"date":"2026-03-02","symbol":"SPY","close":105,"volume":780}])
        r=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                        reference_csv=self.reference,save=False)
        self.assertIn("INVALID_OR_DUPLICATE_LOCAL_BARS",r["review_reasons"])
        self.assertEqual(r["compared_symbol_days"],0)
        self.assertFalse(r["reference_prices_reconciled"])

    def test_bad_interior_values_or_off_grid_bucket_block(self):
        for sql in ("UPDATE rows SET timestamp_ms=timestamp_ms+1 WHERE timestamp_ms=(SELECT min(timestamp_ms)+600000 FROM rows)",
                    "UPDATE rows SET close='NaN'::DOUBLE WHERE timestamp_ms=(SELECT min(timestamp_ms)+600000 FROM rows)",
                    "UPDATE rows SET volume_shares=-1 WHERE timestamp_ms=(SELECT min(timestamp_ms)+600000 FROM rows)"):
            with self.subTest(sql=sql):
                if self.source.exists():
                    self.source.unlink()
                self.seed(mutation=sql)
                self.write([{"date":"2026-03-02","symbol":"SPY","close":105,"volume":780}])
                r=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                                reference_csv=self.reference,save=False)
                self.assertIn("INVALID_OR_DUPLICATE_LOCAL_BARS",r["review_reasons"])
                self.assertFalse(r["reference_prices_reconciled"])

    def test_distinct_source_minutes_are_required(self):
        self.seed(mutation="UPDATE rows SET unique_source_minutes=4 WHERE timestamp_ms=(SELECT min(timestamp_ms)+600000 FROM rows)")
        self.write([{"date":"2026-03-02","symbol":"SPY","close":105,"volume":780}])
        r=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                        reference_csv=self.reference,save=False)
        self.assertEqual(r["compared_symbol_days"],1)
        self.assertEqual(r["volume_compared_symbol_days"],0)
        self.assertIsNone(r["per_symbol"][0]["max_volume_difference_pct"])
        self.assertIn("PARTIAL_RTH_VOLUME_NOT_COMPARABLE",r["review_reasons"])
        self.assertFalse(r["reference_prices_reconciled"])

    def test_zero_reference_volume_not_silently_skipped(self):
        self.seed()
        self.write([{"date":"2026-03-02","symbol":"SPY","close":105,"volume":0}])
        r=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                        reference_csv=self.reference,save=False)
        self.assertIn("REFERENCE_VOLUME_DIVERGENCE",r["review_reasons"])
        self.assertEqual(r["per_symbol"][0]["zero_reference_volume_mismatches"],1)
        self.assertEqual(r["volume_compared_symbol_days"],0)
        self.assertIsNone(r["per_symbol"][0]["max_volume_difference_pct"])
        self.assertFalse(r["reference_prices_reconciled"])
        self.source.unlink()
        self.seed(mutation="UPDATE rows SET volume_shares=0")
        r=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                        reference_csv=self.reference,save=False)
        self.assertEqual(r["volume_compared_symbol_days"],1)
        self.assertEqual(r["per_symbol"][0]["max_volume_difference_pct"],0)
        self.assertTrue(r["reference_prices_reconciled"])

    def test_duplicate_reference_days_fail_closed(self):
        self.seed()
        record={"date":"2026-03-02","symbol":"SPY","close":105,"volume":780}
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
        self.write([{"date":"2026-03-03","symbol":"SPY","close":105,"volume":780}])
        report=hf_stock5m_reconcile.reconcile("2026-03",symbols=["SPY"],
                                              reference_csv=self.reference,save=False)
        self.assertIn("UNMATCHED_REFERENCE_DATES",report["review_reasons"])
        self.assertEqual(0,report["compared_symbol_days"])


if __name__=="__main__":
    unittest.main()
