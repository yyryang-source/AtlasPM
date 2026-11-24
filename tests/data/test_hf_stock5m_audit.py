"""Realistic offline synthetic NYSE audit tests; no HTTP or market data."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m, hf_stock5m_audit

try:
    import duckdb
    import pandas_market_calendars
except ImportError:
    duckdb = None


@unittest.skipUnless(duckdb is not None,"Install optional lake/calendar extras")
class HuggingFaceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.prev = Path.cwd()
        os.chdir(self.temp.name)
        Path("local_data").mkdir()
        self.out = hf_stock5m.output_path("2026-03")
        self.out.parent.mkdir(parents=True,exist_ok=True)
    def tearDown(self):
        os.chdir(self.prev)
        self.temp.cleanup()

    def seed(self,*,before_open=False,bad_ohlc=False):
        cx=duckdb.connect()
        offset=-5 if before_open else 0
        cx.execute(f"""
            CREATE TABLE bars AS
            SELECT 'AAPL' AS symbol,
                   DATE '2026-03-02' AS trade_date_et,
                   CAST(epoch_ms(TIMESTAMPTZ '2026-03-02 14:30:00+00'
                       + (i+{offset}) * INTERVAL '5 minute') AS BIGINT) AS timestamp_ms,
                   100.0::DOUBLE AS open,
                   102.0::DOUBLE AS high,
                   99.0::DOUBLE AS low,
                   101.0::DOUBLE AS close,
                   300.0::DOUBLE AS volume_shares,
                   NULL::BIGINT AS transactions,
                   5::BIGINT AS source_minute_bars,
                   5::BIGINT AS unique_source_minutes
            FROM range(4) t(i)
        """)
        cx.execute("""
            INSERT INTO bars
            VALUES ('MSFT',DATE '2026-03-02',
                 epoch_ms(TIMESTAMPTZ '2026-03-02 14:30:00+00'),
                 200,201,199,200.5,250,NULL,3,3)
        """)
        if bad_ohlc:
            cx.execute("UPDATE bars SET high=90 WHERE symbol='MSFT'")
        cx.execute(f"COPY bars TO '{hf_stock5m._quote(self.out)}' (FORMAT PARQUET)")
        cx.close()
        quarantine,meta=hf_stock5m.quality_paths(self.out)
        quarantine.parent.mkdir(parents=True,exist_ok=True)
        with meta.open("w",encoding="utf-8") as f:
            json.dump({"excluded_ambiguous_5m_buckets":1},f)
        # The quarantine object itself is checked only for existence here.
        quarantine.write_bytes(b"fixture")

    def test_audit_reports_missing_calendar_days_and_benchmark_gaps(self):
        self.seed()
        report=hf_stock5m_audit.audit("2026-03")
        self.assertEqual("REVIEW_REQUIRED",report["quality_status"])
        self.assertEqual(5,report["five_minute_bars"])
        self.assertEqual(2,report["unique_symbols"])
        self.assertEqual(1,report["observed_trade_dates"])
        self.assertEqual(0,report["invalid_five_minute_bars"])
        self.assertEqual(0,report["duplicate_symbol_timestamp_rows"])
        self.assertEqual(0,report["out_of_nyse_session_bars"])
        self.assertEqual(1,report["ambiguous_buckets_quarantined"])
        self.assertIn("MISSING_WHOLE_NYSE_SESSIONS",report["review_reasons"])
        self.assertFalse(report["approved_for_model_training"])
        self.assertTrue(Path(report["audit_report_path"]).is_file())
        aapl=next(x for x in report["benchmark_coverage"] if x["symbol"]=="AAPL")
        spy=next(x for x in report["benchmark_coverage"] if x["symbol"]=="SPY")
        self.assertEqual(4,aapl["five_minute_bars"])
        self.assertEqual(0,spy["five_minute_bars"])
        self.assertGreater(aapl["expected_rth_five_minute_slots"],4)

    def test_source_anomaly_quarantine_is_explicit_in_audit(self):
        self.seed()
        _,meta_path=hf_stock5m.quality_paths(self.out)
        with meta_path.open("w",encoding="utf-8") as f:
            json.dump({
                "excluded_ambiguous_5m_buckets": 0,
                "excluded_invalid_source_5m_buckets": 1,
                "excluded_total_5m_buckets": 1,
                "invalid_source_rows": 1
            }, f)
        result=hf_stock5m_audit.audit("2026-03",save=False)
        self.assertEqual(0,result["ambiguous_buckets_quarantined"])
        self.assertEqual(1,result["invalid_source_5m_buckets_quarantined"])
        self.assertEqual(1,result["total_5m_buckets_quarantined"])
        self.assertEqual(1,result["invalid_raw_source_rows"])
        self.assertIn("QUARANTINED_INVALID_SOURCE_5M_BUCKETS",result["review_reasons"])
        self.assertIn("RAW_SOURCE_OHLCV_ANOMALIES",result["review_reasons"])
        self.assertFalse(result["approved_for_model_training"])

    def test_detects_out_of_session_timestamp(self):
        self.seed(before_open=True)
        report=hf_stock5m_audit.audit("2026-03",save=False)
        self.assertEqual("STRUCTURAL_QC_FAILED",report["quality_status"])
        self.assertIn("OUTSIDE_NYSE_SESSIONS:4",report["structural_issues"])
        self.assertNotIn("audit_report_path",report)

    def test_detects_corrupt_ohlc(self):
        self.seed(bad_ohlc=True)
        report=hf_stock5m_audit.audit("2026-03",save=False)
        self.assertEqual(1,report["invalid_five_minute_bars"])
        self.assertEqual("STRUCTURAL_QC_FAILED",report["quality_status"])

    @staticmethod
    def _ms(utc: str) -> int:
        return int(datetime.fromisoformat(utc).timestamp() * 1000)

    def test_six_historical_nyse_non_aligned_sessions_are_supported(self):
        # Regression dates from NYSE calendar historical special hours.
        examples = {
            "2001-09": "2001-09-17",
            "2001-10": "2001-10-08",
            "2003-03": "2003-03-20",
            "2004-06": "2004-06-07",
            "2005-06": "2005-06-01",
            "2006-12": "2006-12-27",
        }
        for month, day in examples.items():
            with self.subTest(month=month):
                entries = hf_stock5m_audit._calendar(month)
                selected = [(start, end) for d, start, end in entries
                            if d.isoformat() == day]
                self.assertEqual(1, len(selected))
                start, end = selected[0]
                self.assertTrue(
                    start % hf_stock5m.FIVE_MINUTES_MS
                    or end % hf_stock5m.FIVE_MINUTES_MS)

    def test_partially_overlapping_late_open_bucket_requires_review(self):
        self.seed()
        start = self._ms("2026-03-02T14:32:00+00:00")
        end = self._ms("2026-03-02T21:00:00+00:00")
        # First UTC 5m bucket is 14:30 and overlaps a 14:32 NYSE open.
        with patch.object(hf_stock5m_audit, "_calendar",
                          return_value=[(date(2026, 3, 2), start, end)]):
            report = hf_stock5m_audit.audit("2026-03", save=False)
        self.assertEqual(0, report["out_of_nyse_session_bars"])
        self.assertEqual(2, report["partial_nyse_boundary_5m_bars"])
        self.assertEqual(1, report["nyse_boundary_minute_count_exceeded"])
        self.assertEqual(78, report["nyse_expected_rth_slots_per_liquid_symbol"])
        self.assertEqual("REVIEW_REQUIRED", report["quality_status"])
        self.assertEqual([], report["structural_issues"])
        self.assertIn("NYSE_BOUNDARY_MINUTES_EXCEED_EXCHANGE_OPEN_VENUE_UNVERIFIED",
                      report["review_reasons"])
        self.assertFalse(report["symbol_primary_listing_exchange_verified"])
        self.assertEqual(1, len(report["non_5m_aligned_nyse_sessions"]))
        self.assertIn("HISTORICAL_NYSE_PARTIAL_5M_SESSION_BOUNDARIES",
                      report["review_reasons"])
        self.assertFalse(report["approved_for_model_training"])
        self.assertFalse(report["live_execution_enabled"])

    def test_genuine_invalid_ohlcv_still_fails_with_venue_uncertainty(self):
        # Never let the venue-unverified boundary issue suppress an unrelated,
        # independently provable invalid OHLC bar.
        self.seed(bad_ohlc=True)
        start = self._ms("2026-03-02T14:32:00+00:00")
        end = self._ms("2026-03-02T21:00:00+00:00")
        with patch.object(hf_stock5m_audit, "_calendar",
                          return_value=[(date(2026, 3, 2), start, end)]):
            report = hf_stock5m_audit.audit("2026-03", save=False)
        self.assertEqual("STRUCTURAL_QC_FAILED", report["quality_status"])
        self.assertIn("INVALID_5M_BARS:1", report["structural_issues"])
        self.assertEqual(1, report["nyse_boundary_minute_count_exceeded"])
        self.assertIn("NYSE_BOUNDARY_MINUTES_EXCEED_EXCHANGE_OPEN_VENUE_UNVERIFIED",
                      report["review_reasons"])
        self.assertFalse(report["approved_for_model_training"])
        self.assertFalse(report["live_execution_enabled"])

    def test_possible_valid_partial_bucket_stays_review_only(self):
        self.seed()
        cx = duckdb.connect()
        cx.execute("""
            CREATE TABLE updated AS
            SELECT symbol,trade_date_et,timestamp_ms,open,high,low,close,
                   volume_shares,transactions,source_minute_bars,unique_source_minutes
            FROM read_parquet(?)
        """, [str(self.out)])
        cx.execute("""
            UPDATE updated SET source_minute_bars=3,unique_source_minutes=3
            WHERE symbol='AAPL' AND open=100
        """)
        cx.execute("""
            UPDATE updated SET source_minute_bars=2,unique_source_minutes=2
            WHERE symbol='MSFT'
        """)
        self.out.unlink()
        cx.execute(f"COPY updated TO '{hf_stock5m._quote(self.out)}' (FORMAT PARQUET)")
        cx.close()
        start = self._ms("2026-03-02T14:32:00+00:00")
        end = self._ms("2026-03-02T21:00:00+00:00")
        with patch.object(hf_stock5m_audit, "_calendar",
                          return_value=[(date(2026, 3, 2), start, end)]):
            result = hf_stock5m_audit.audit("2026-03", save=False)
        self.assertEqual(0, result["nyse_boundary_minute_count_exceeded"])
        self.assertEqual(0, result["out_of_nyse_session_bars"])
        self.assertEqual([], result["structural_issues"])
        self.assertEqual("REVIEW_REQUIRED", result["quality_status"])
        self.assertIn("PARTIAL_NYSE_BOUNDARY_5M_BARS_REQUIRE_SOURCE_REVIEW",
                      result["review_reasons"])

    def test_partial_early_close_bucket_not_falsely_out_of_session(self):
        self.seed()
        cx = duckdb.connect()
        cx.execute("""
            CREATE TABLE updated AS
            SELECT symbol,trade_date_et,timestamp_ms,open,high,low,close,
                   volume_shares,transactions,source_minute_bars,unique_source_minutes
            FROM read_parquet(?)
        """, [str(self.out)])
        cx.execute("""
            INSERT INTO updated
            VALUES ('AAPL', DATE '2026-03-02',
                    epoch_ms(TIMESTAMPTZ '2026-03-02 20:55:00+00'),
                    100,102,99,101,300,NULL,1,1)
        """)
        self.out.unlink()
        cx.execute(f"COPY updated TO '{hf_stock5m._quote(self.out)}' (FORMAT PARQUET)")
        cx.close()
        start = self._ms("2026-03-02T14:30:00+00:00")
        end = self._ms("2026-03-02T20:56:00+00:00")
        with patch.object(hf_stock5m_audit, "_calendar",
                          return_value=[(date(2026, 3, 2), start, end)]):
            report = hf_stock5m_audit.audit("2026-03", save=False)
        self.assertEqual(0, report["out_of_nyse_session_bars"])
        self.assertEqual(1, report["partial_nyse_boundary_5m_bars"])
        self.assertEqual(0, report["nyse_boundary_minute_count_exceeded"])
        self.assertEqual("REVIEW_REQUIRED", report["quality_status"])
        self.assertEqual(78, report["nyse_expected_rth_slots_per_liquid_symbol"])

    def test_requires_existing_file(self):
        with self.assertRaisesRegex(ValueError,"Local 5m Parquet not found"):
            hf_stock5m_audit.audit("2026-03",save=False)


if __name__=="__main__":
    unittest.main()
