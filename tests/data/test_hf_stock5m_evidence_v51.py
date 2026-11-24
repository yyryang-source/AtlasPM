"""V5.1 offline collection tests; never call external providers."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m_evidence_v51 as ev

INDEX = (b"Description\nCIK|Company Name|Form Type|Date Filed|Filename\n"
         b"----\n123|Example|8-K|2000-01-03|edgar/data/123/0000000123-00-000001.txt\n"
         b"123|Example|10-K|2000-02-01|edgar/data/123/0000000123-00-000002.txt\n")
INDEX2 = INDEX.replace(b"2000-01-03",b"2000-04-03").replace(b"2000-02-01",b"2000-05-01")


class EvidenceCollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir, self.previous)
        Path("local_data").mkdir()

    def test_full_history_plan_no_network(self):
        with patch.object(ev, "download") as fetch:
            result = ev.run()
        self.assertEqual(result["tasks_planned"],108)
        self.assertEqual(result["network_downloads"],0)
        self.assertFalse(result["approved_for_model_training"])
        fetch.assert_not_called()

    def test_filing_candidates_not_actions(self):
        stats, rows = ev.parse_sec_index(INDEX,"2000-Q1")
        self.assertEqual(stats,{"filings":2,"candidate_filings":1})
        self.assertEqual(rows[0]["cik"],"0000000123")
        self.assertEqual(rows[0]["filed_date"],"2000-01-03")
        self.assertNotIn("security_id",rows[0])
        with self.assertRaises(ValueError):
            ev.parse_sec_index(INDEX,"2000-Q2")

    def test_budget_resume_and_cache_integrity(self):
        args = dict(current=False,fetch=True,max_requests=1,agent="AtlasPM test test@example.com")
        with patch.object(ev,"download",side_effect=[INDEX,INDEX2]) as fetch, patch.object(ev.time,"sleep"):
            first = ev.run("2000-Q1","2000-Q2",**args)
            second = ev.run("2000-Q1","2000-Q2",**args)
            third = ev.run("2000-Q1","2000-Q2",**args)
        self.assertEqual(first["tasks_completed"],1)
        self.assertEqual(second["tasks_completed"],2)
        self.assertEqual(third["network_downloads"],0)
        self.assertEqual(fetch.call_count,2)
        self.assertFalse(second["approved_for_model_training"])
        self.assertFalse(second["live_execution_enabled"])
        Path(second["results"][0]["raw_path"]).write_bytes(b"corrupt")
        with patch.object(ev,"download") as fetch:
            bad = ev.run("2000-Q1","2000-Q2",**args)
        self.assertEqual(bad["status"],"PARTIAL_OR_BLOCKED")
        self.assertIn("hash mismatch",bad["results"][0]["detail"])
        fetch.assert_not_called()

    def test_listing_date_and_response_guards(self):
        with self.assertRaises(ValueError):
            ev.plan("2000-Q1","2000-Q1",["2009-01-01"],False)
        with self.assertRaises(ValueError):
            ev.parse_listing(b'{"Note":"rate limit"}')
        data = b"symbol,name,exchange,assetType,ipoDate,delistingDate,status\nABC,Example,NYSE,Stock,2012-01-01,null,Active\n"
        stats, rows = ev.parse_listing(data)
        self.assertEqual(stats["vendor_listing_rows"],1)
        self.assertNotIn("security_id",rows[0])

    def test_provider_failure_and_no_secret_in_report(self):
        with patch.dict(os.environ,{"ALPHAVANTAGE_API_KEY":"SECRET"}), patch.object(ev,"download",side_effect=ValueError("Provider HTTP 429")) as fetch:
            r = ev.run("2000-Q1","2000-Q2",listing_dates=["2020-01-01"],
                       current=False,fetch=True,agent="AtlasPM test test@example.com")
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(r["status"],"PARTIAL_OR_BLOCKED")
        self.assertNotIn("SECRET",json.dumps(r))
        self.assertFalse(r["data_rights_verified"])

    def test_sec_current_issuer_not_security_identity(self):
        stats, rows = ev.parse_sec_tickers(b'{"fields":["cik","name","ticker","exchange"],"data":[[123,"Example","ABC","NYSE"]]}')
        self.assertEqual(stats["current_issuer_ticker_rows"],1)
        self.assertNotIn("security_id",rows[0])
