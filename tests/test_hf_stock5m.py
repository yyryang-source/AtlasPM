"""Offline quality and query tests: never contact Hugging Face."""
from __future__ import annotations
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m as mod


class HuggingFaceStock5mTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=Path.cwd()
        os.chdir(self.temp.name)
        Path("local_data").mkdir()
    def tearDown(self):
        os.chdir(self.old)
        self.temp.cleanup()

    def test_plan_1992_through_2026_without_network(self):
        with patch.object(mod,"download",side_effect=AssertionError("network")):
            plan=mod.backfill("2000-01","2026-11",max_months=0,dry_run=True)
        self.assertEqual("2026-03",plan["months_selected"][-1])
        self.assertEqual("2000-01",plan["months_selected"][0])
        self.assertEqual(315,len(plan["months_selected"]))
        self.assertFalse(plan["coverage_verified"])
        self.assertFalse(plan["data_rights_verified"])

    def test_default_caps_to_one_month(self):
        plan=mod.backfill("2025-01","2025-12",dry_run=True)
        self.assertEqual(["2025-01"],plan["months_selected"])

    def test_bad_month_and_range(self):
        for invalid in ("2025-00","2025-13","2025-1","2025-01-01","2000-X1"):
            with self.assertRaises(ValueError): mod.validate_month(invalid)
        with self.assertRaises(ValueError): mod.months("2025-12","2025-01")
        with self.assertRaises(ValueError): mod.months("2025-01","2025-02",-1)

    def test_network_requires_data_rights_acknowledgement(self):
        with patch.object(mod,"download",side_effect=AssertionError("network")):
            with self.assertRaisesRegex(ValueError,"NOT a data license"):
                mod.backfill("2026-03","2026-03")

    def test_sql_aggregates_true_minute_ohlcv(self):
        query=mod.source_sql(mod.raw_path("2026-03"),"rth")
        for fragment in ("arg_min(open,timestamp)","arg_max(close,timestamp)",
                         "max(high)","min(low)","sum(volume)","count(*)",
                         "09:30:00","16:00:00","America/New_York",
                         "count(DISTINCT epoch_ms(timestamp))"):
            self.assertIn(fragment,query)

    def test_no_network_when_output_exists(self):
        out=mod.output_path("2026-03")
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_bytes(b"x"*150)
        with patch.object(mod,"download",side_effect=AssertionError("network")):
            report=mod.backfill("2026-03","2026-03",acknowledge_rights=True)
        self.assertEqual("existing",report["files"][0]["state"])


try:
    import duckdb
except ImportError:
    duckdb=None


@unittest.skipUnless(duckdb is not None,"Requires optional DuckDB lake dependency")
class DuckDbHuggingFaceConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=Path.cwd()
        os.chdir(self.tmp.name)
        Path("local_data").mkdir()
        self.raw=mod.raw_path("2026-03")
        self.raw.parent.mkdir(parents=True,exist_ok=True)
        self.out=mod.output_path("2026-03")
    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def seed(self,*,duplicate=False,invalid=False,extra_symbols=0):
        cx=duckdb.connect()
        cx.execute("""
            CREATE TABLE bars AS
            SELECT 'AAPL' AS ticker,
                   (TIMESTAMPTZ '2026-03-02 14:30:00+00' + i * INTERVAL '1 minute') AS timestamp,
                   CAST(100+i AS DOUBLE) AS open,
                   CAST(102+i AS DOUBLE) AS high,
                   CAST(99+i AS DOUBLE) AS low,
                   CAST(101+i AS DOUBLE) AS close,
                   100.0::DOUBLE AS volume
            FROM range(5) t(i)
        """)
        cx.execute("""
            INSERT INTO bars VALUES ('MSFT', TIMESTAMPTZ '2026-03-02 14:30:00+00',
                                     200, 201, 199, 200.5, 40)
        """)
        if extra_symbols:
            cx.execute("""
                INSERT INTO bars
                SELECT 'S' || lpad(CAST(i AS VARCHAR),5,'0'),
                       TIMESTAMPTZ '2026-03-02 14:30:00+00',
                       50.0,51.0,49.0,50.5,12.0
                FROM range(?) t(i)
            """,[extra_symbols])
        if duplicate: cx.execute("INSERT INTO bars SELECT * FROM bars WHERE ticker='AAPL' LIMIT 1")
        if invalid: cx.execute("UPDATE bars SET low=999 WHERE ticker='MSFT'")
        cx.execute(f"COPY bars TO '{mod._quote(self.raw)}' (FORMAT PARQUET)")
        cx.close()

    def test_conversion(self):
        self.seed()
        result=mod.convert(self.raw,self.out)
        self.assertEqual(6,result["source_rows"])
        self.assertEqual(2,result["bars_5m"])
        cx=duckdb.connect()
        row=cx.execute("SELECT open,high,low,close,volume_shares,source_minute_bars "
                       "FROM read_parquet(?) WHERE symbol='AAPL'",[str(self.out)]).fetchone()
        cx.close()
        self.assertEqual((100.0,106.0,99.0,105.0,500.0,5),row)

    def test_many_ambiguous_minutes_rejected_without_output(self):
        self.seed(duplicate=True)
        with self.assertRaisesRegex(ValueError,"limit 0.1000%"):
            mod.convert(self.raw,self.out)
        self.assertFalse(self.out.exists())
        quarantine,audit=mod.quality_paths(self.out)
        self.assertFalse(quarantine.exists())
        self.assertFalse(audit.exists())
        self.assertTrue(self.raw.exists())

    def test_sparse_ambiguous_minute_quarantined(self):
        self.seed(duplicate=True,extra_symbols=2000)
        result=mod.convert(self.raw,self.out)
        self.assertEqual(2002,result["candidate_bars_5m"])
        self.assertEqual(2001,result["bars_5m"])
        self.assertEqual(1,result["excluded_ambiguous_5m_buckets"])
        self.assertEqual("REVIEW_REQUIRED",result["quality_status"])
        quarantine,audit=mod.quality_paths(self.out)
        self.assertTrue(quarantine.is_file())
        self.assertTrue(audit.is_file())
        cx=duckdb.connect()
        bad=cx.execute("SELECT symbol,source_minute_bars,unique_source_minutes "
                       "FROM read_parquet(?)",[str(quarantine)]).fetchall()
        kept_aapl=cx.execute("SELECT count(*) FROM read_parquet(?) WHERE symbol='AAPL'",
                             [str(self.out)]).fetchone()[0]
        cx.close()
        self.assertEqual([("AAPL",6,5)],bad)
        self.assertEqual(0,kept_aapl)
        self.assertEqual(1,mod.status()["quarantined_5m_buckets"])
        self.assertTrue(self.raw.exists())

    def test_clean_refresh_clears_obsolete_quarantine(self):
        self.seed(duplicate=True,extra_symbols=2000)
        mod.convert(self.raw,self.out)
        self.raw.unlink()
        self.seed(duplicate=False)
        new=mod.convert(self.raw,self.out)
        self.assertEqual(0,new["excluded_ambiguous_5m_buckets"])
        quarantine,audit=mod.quality_paths(self.out)
        self.assertFalse(quarantine.exists())
        self.assertTrue(audit.is_file())
        self.assertEqual(0,mod.status()["quarantined_5m_buckets"])

    def test_invalid_ohlc_rejected_without_output(self):
        self.seed(invalid=True)
        with self.assertRaisesRegex(ValueError,"Source QC failed"):
            mod.convert(self.raw,self.out)
        self.assertFalse(self.out.exists())


if __name__=="__main__":
    unittest.main()
