"""Realistic offline synthetic NYSE audit tests; no HTTP or market data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

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

    def test_requires_existing_file(self):
        with self.assertRaisesRegex(ValueError,"Local 5m Parquet not found"):
            hf_stock5m_audit.audit("2026-03",save=False)


if __name__=="__main__":
    unittest.main()
