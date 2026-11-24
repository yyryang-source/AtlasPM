"""Offline tests for V5.4 market anomaly discovery and filing budgets."""
from datetime import date
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m_discovery_v54 as d
from atlaspm.data import hf_stock5m_evidence_v51 as ev


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        old=Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir,old)
        Path("local_data").mkdir()

    def test_prior_month(self):
        self.assertEqual(d.prior_month("2020-01"),"2019-12")

    def test_daily_candidates_boundaries_and_limit(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("Install lake extras")
        cx=duckdb.connect()
        try:
            cx.execute("""CREATE TABLE daily(symbol VARCHAR,trade_date DATE,
                day_open DOUBLE,day_close DOUBLE,volume DOUBLE,bars BIGINT,
                unique_bars BIGINT,first_ms BIGINT,last_ms BIGINT)""")
            cx.executemany("INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?)",[
                ("ABC","2020-01-31",10,10,100,2,2,0,300000),
                ("ABC","2020-02-03",20,20,100,2,2,600000,900000),
                ("SPARSE","2020-01-31",10,10,100,1,1,0,0),
                ("SPARSE","2020-02-03",100,100,100,2,2,600000,900000),
                ("FLAT","2020-01-31",10,10,100,2,2,0,300000),
                ("FLAT","2020-02-03",10,10,100,2,2,600000,900000)])
            p=Path("local_data/day.parquet").resolve()
            cx.execute("COPY daily TO "+d.factors._quoted(p)+" (FORMAT PARQUET)")
            calendar=[(date(2020,1,31),0,600000,False),
                      (date(2020,2,3),600000,1200000,False)]
            rows,total=d._candidates(cx,[p],calendar,"2020-02",1.5,1)
            self.assertEqual(total,1)
            self.assertEqual(rows[0]["symbol"],"ABC")
            self.assertEqual(rows[0]["split_like_price_ratio"],2)
            self.assertEqual(rows[0]["status"],"UNVERIFIED_MARKET_OBSERVATION")
        finally:
            cx.close()

    def queue(self):
        ev._json(d.BASE/"queues"/"2020-02.json",{
            "schema":"atlaspm_market_event_discovery_v54","month":"2020-02",
            "candidates":[{"event_id":str(i),"symbol":s,"date":"2020-02-03",
                           "kind":"OVERNIGHT_PRICE_GAP"}
                          for i,s in enumerate(("ABC","DEF","OLD"))]})

    def test_snapshot_hash_and_ambiguity(self):
        data=b'{"fields":["cik","name","ticker","exchange"],"data":[[123,"Example","ABC","NYSE"],[456,"Other","ABC","Nasdaq"]]}'
        digest=ev._sha(data)
        ev._write(ev.BASE/"objects"/(digest+".bin"),data)
        ev._json(ev.BASE/"manifests"/"sec-current-2020-02-03.json",{
            "source_url":ev.SEC_TICKERS,"scope":"CURRENT_ISSUER_MAPPING",
            "sha256":digest,"retrieved_utc":"2020-02-03"})
        mapping,_=d.issuer_leads()
        self.assertEqual(mapping["ABC"],{"0000000123","0000000456"})
        ev._write(ev.BASE/"objects"/(digest+".bin"),b"bad")
        with self.assertRaises(ValueError):
            d.issuer_leads()

    def test_global_budget_and_missing_leads(self):
        self.queue()
        mapping={"ABC":{"0000000123"},"DEF":{"0000000456"}}
        response={"filings":[{}],"results":[],"filings_parsed":1,"hints_count":2,
                  "missing_requested_quarters":[],"truncated":False,
                  "requests_attempted":1,"network_downloads":1}
        offline={**response,"requests_attempted":0,"network_downloads":0}
        with patch.object(d,"issuer_leads",return_value=(mapping,{})), patch.object(d.filings,"collect",side_effect=[response,offline]) as collect:
            r=d.investigate("2020-02",fetch=True,max_requests=1)
        self.assertEqual(collect.call_count,2)
        self.assertTrue(collect.call_args_list[0].kwargs["fetch"])
        self.assertNotIn("fetch",collect.call_args_list[1].kwargs)
        self.assertEqual(r["requests_attempted"],1)
        self.assertEqual(r["results"][2]["status"],"NO_UNAMBIGUOUS_ISSUER_SEARCH_LEAD")
        self.assertFalse(r["approved_for_model_training"])
        self.assertFalse(r["live_execution_enabled"])

    def test_network_failure_stops_further_requests(self):
        self.queue()
        response={"filings":[{}],"results":[],"filings_parsed":0,"hints_count":0,
                  "missing_requested_quarters":[],"truncated":False,
                  "requests_attempted":1,"network_downloads":0}
        offline={**response,"requests_attempted":0}
        with patch.object(d,"issuer_leads",return_value=({"ABC":{"1"},"DEF":{"2"}},{})), patch.object(d.filings,"collect",side_effect=[response,offline]) as collect:
            r=d.investigate("2020-02",fetch=True)
        self.assertTrue(r["network_stopped"])
        self.assertNotIn("fetch",collect.call_args_list[1].kwargs)
