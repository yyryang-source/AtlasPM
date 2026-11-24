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


    def test_isolated_index_row_quarantine(self):
        header = b"CIK|Company Name|Form Type|Date Filed|Filename\n"
        valid = b"123|Example|8-K|2016-01-04|edgar/data/123/0000000123-16-000001.txt\n"
        invalid = b"123|Example|8-K|2015-12-31|edgar/data/123/0000000123-15-000001.txt\n"
        data = header + valid * 2000 + invalid
        stats, rows = ev.parse_sec_index(data, "2016-Q1")
        self.assertEqual(stats["filings"], 2000)
        self.assertEqual(stats["rejected_rows"], 1)
        self.assertEqual(stats["rejection_reasons"], {"OUTSIDE_REQUESTED_QUARTER": 1})
        self.assertEqual(len(rows), 2000)
        self.assertEqual(stats["rejection_examples"][0]["line"], 2002)
        with self.assertRaises(ev.EvidenceParseError):
            ev.parse_sec_index(header + valid + invalid, "2016-Q1")

    def test_bad_quarter_does_not_block_next_quarter(self):
        with patch.object(ev, "download", side_effect=[b"bad index", INDEX2]) as fetch, patch.object(ev.time, "sleep"):
            result = ev.run("2000-Q1", "2000-Q2", current=False, fetch=True,
                            agent="AtlasPM test test@example.com")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["tasks_completed"], 1)
        self.assertEqual(result["blocked_providers"], [])
        self.assertEqual(result["status"], "PARTIAL_OR_BLOCKED")
        self.assertEqual(result["results"][0]["error"], "EvidenceParseError")
        self.assertEqual(len(list(Path(ev.BASE / "rejected").glob("*.json"))), 1)

    def test_review_status_for_small_quarantine(self):
        header = b"CIK|Company Name|Form Type|Date Filed|Filename\n"
        valid = b"123|Example|8-K|2000-01-04|edgar/data/123/0000000123-00-000001.txt\n"
        with patch.object(ev, "download", return_value=header + valid * 2000 + b"bad row\n"), patch.object(ev.time, "sleep"):
            result = ev.run("2000-Q1", "2000-Q1", current=False, fetch=True,
                            agent="AtlasPM test test@example.com")
        self.assertEqual(result["status"], "CANDIDATES_COLLECTED_WITH_REVIEW")
        self.assertEqual(result["total_rejected_rows"], 1)
        self.assertFalse(result["approved_for_model_training"])
