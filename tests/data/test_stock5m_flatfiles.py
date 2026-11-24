"""Offline tests: do not call the Massive network or send credentials."""
from __future__ import annotations
import csv,gzip,os,tempfile,unittest
from datetime import date,datetime,timezone
from pathlib import Path
from unittest.mock import patch
from atlaspm.data import stock5m_flatfiles as mod

class MarketFiveMinuteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.original=Path.cwd()
        os.chdir(self.tmp.name)
        Path("local_data").mkdir()
    def tearDown(self):
        os.chdir(self.original)
        self.tmp.cleanup()
    def test_floor_2003_no_fake_2000_bars(self):
        plan=mod.backfill("2000-01-01","2003-09-12",dry_run=True)
        self.assertEqual("2003-09-10",plan["source_floor"])
        self.assertTrue(plan["source_before_2003_unavailable"])
        self.assertEqual(3,plan["selected_sessions"])
        self.assertIn("2003/09/2003-09-10.csv.gz",plan["first_object"])
    def test_sql_uses_real_earliest_and_last_ohlc(self):
        raw,out=mod.paths(date(2026,11,20))
        sql=mod.sql(raw,out,date(2026,11,20),"rth")
        for keyword in ("arg_min(open,window_start)","arg_max(close,window_start)",
                        "max(high)","min(low)","sum(volume)","source_minute_bars",
                        "09:30:00","16:00:00"):
            self.assertIn(keyword,sql)
    def test_probe_does_not_reveal_key(self):
        with patch.object(mod,"key_from_env",return_value="secret-for-test"), \
             patch.object(mod,"get_json",return_value={"results":[{"t":123}]}) as client:
            response=mod.probe("AAPL","2026-11-20")
        self.assertEqual(1,response["five_minute_bars"])
        self.assertNotIn("secret-for-test",str(response))
        self.assertIn("adjusted=false",client.call_args.args[0])
    def test_dry_run_never_networks(self):
        with patch.object(mod,"s3",side_effect=AssertionError("network in dry-run")):
            response=mod.backfill("2026-11-19","2026-11-20",max_days=1,dry_run=True)
        self.assertEqual("dry_run",response["mode"])
    def test_empty_dates_and_bad_order(self):
        self.assertEqual([],mod.sessions("2000-01-01","2000-01-02",dry=True)[0])
        with self.assertRaisesRegex(ValueError,"start later"):
            mod.sessions("2026-11-20","2026-11-19",dry=True)

try:
    import duckdb
except ImportError:
    duckdb=None

@unittest.skipUnless(duckdb is not None,"Optional DuckDB dependency required")
class ParquetConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.previous=Path.cwd()
        os.chdir(self.temp.name)
        Path("local_data").mkdir()
        self.raw,self.out=mod.paths(date(2026,11,20))
        self.raw.parent.mkdir(parents=True)
        epoch=int(datetime(2026,11,20,14,30,tzinfo=timezone.utc).timestamp())*1_000_000_000
        with gzip.open(self.raw,"wt",newline="") as f:
            writer=csv.writer(f)
            writer.writerow(["ticker","volume","open","close","high","low","window_start","transactions"])
            for i in range(5):
                writer.writerow(["AAPL",100,10+i,11+i,12+i,9+i,epoch+i*60_000_000_000,2])
            writer.writerow(["MSFT",200,20,22,23,19,epoch,4])
    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()
    def test_ohlcv_and_true_minute_count(self):
        self.assertEqual(2,mod.convert(self.raw,self.out,date(2026,11,20),"rth"))
        con=duckdb.connect(":memory:")
        row=con.execute("SELECT open,high,low,close,volume_shares,source_minute_bars "
                        "FROM read_parquet(?) WHERE symbol='AAPL'",[str(self.out)]).fetchone()
        con.close()
        self.assertEqual((10.0,16.0,9.0,15.0,500.0,5),row)

if __name__=="__main__":
    unittest.main()
