"""Offline SEC index and filing intelligence tests."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m_evidence_v51 as ev
from atlaspm.data import hf_stock5m_filings_v52 as f

URL = "https://www.sec.gov/Archives/edgar/data/123/0000000123-20-000001.txt"
INDEX = (b"CIK|Company Name|Form Type|Date Filed|Filename\n"
         b"123|Example|8-K|2020-01-03|edgar/data/123/0000000123-20-000001.txt\n"
         b"124|Other|25|2020-01-04|edgar/data/124/0000000124-20-000002.txt\n")
BODY = (b"<SEC-DOCUMENT>example\n<DOCUMENT>\n<TYPE>8-K\n<TEXT>"
        b"<p>Proposed 1-for-25 reverse stock split. A cash dividend was discussed.</p>"
        b"<p>Previously a 2-for-1 stock split occurred.</p>"
        b"</TEXT></DOCUMENT>\n<DOCUMENT>\n<TYPE>EX-99\n<TEXT>"
        b"completed the merger</TEXT></DOCUMENT></SEC-DOCUMENT>")


class FilingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir,self.old)
        Path("local_data").mkdir()

    def seed(self):
        digest = ev._sha(INDEX)
        ev._write(ev.BASE/"objects"/(digest+".bin"),INDEX)
        ev._json(ev.BASE/"manifests"/"sec-2020-Q1.json",{
            "task":"sec-2020-Q1","scope":"HISTORICAL_FILING_INDEX",
            "source_url":"https://www.sec.gov/Archives/edgar/full-index/2020/QTR1/master.idx",
            "sha256":digest})
        return digest

    def require_duckdb(self):
        try:
            __import__("duckdb")
        except ImportError:
            self.skipTest("Install lake extras")

    def test_build_reuse_and_query(self):
        self.require_duckdb()
        self.seed()
        result = f.build("2020-Q1","2020-Q1")
        self.assertEqual(result["quarters_completed"],1)
        self.assertEqual(result["candidate_rows"],2)
        result = f.build("2020-Q1","2020-Q1")
        self.assertEqual(result["quarters"][0]["status"],"REUSED")
        s = f.select(["123"],"2020-01-01","2020-06-30","split",1)
        self.assertEqual(len(s["filings"]),1)
        self.assertEqual(s["filings"][0]["source_url"],URL)
        self.assertEqual(s["missing_requested_quarters"],["2020-Q2"])
        self.assertFalse(s["approved_for_model_training"])

    def test_hash_and_missing_partition(self):
        self.require_duckdb()
        digest = self.seed()
        ev._write(ev.BASE/"objects"/(digest+".bin"),b"bad")
        r = f.build("2020-Q1","2020-Q2")
        self.assertEqual(r["status"],"PARTIAL_INDEX")
        self.assertEqual(r["quarters_completed"],0)

    def test_extract_primary_only_and_no_invented_events(self):
        filing = {"form":"8-K","filed_date":"2020-01-03"}
        r = f.extract(BODY,filing)
        kinds = {x["kind"] for x in r["hints"]}
        self.assertIn("SPLIT_RATIO",kinds)
        self.assertIn("DIVIDEND",kinds)
        self.assertNotIn("MERGER",kinds)
        self.assertTrue(r["multiple_ratio_candidates"])
        self.assertTrue(all(x["effective_date"] is None and x["security_id"] is None for x in r["hints"]))
        with self.assertRaises(ValueError):
            f.extract(b"<html>access denied</html>",filing)
        with self.assertRaises(ValueError):
            f.extract(BODY,{"form":"25","filed_date":"2020-01-03"})

    def test_offline_then_cached_body(self):
        self.require_duckdb()
        self.seed()
        f.build("2020-Q1","2020-Q1")
        args = (["123"],"2020-01-01","2020-03-31","split")
        with patch.object(ev,"download") as download:
            report = f.collect(*args)
            download.assert_not_called()
        self.assertEqual(report["results"][0]["status"],"NOT_CACHED")
        with patch.dict(os.environ,{"ATLASPM_SEC_USER_AGENT":"Test test@example.com"}), patch.object(ev,"download",return_value=BODY) as download, patch.object(f.time,"sleep"):
            report = f.collect(*args,fetch=True,max_requests=1)
            download.assert_called_once()
        self.assertEqual(report["filings_parsed"],1)
        self.assertGreater(report["hints_count"],0)
        with patch.object(ev,"download") as download:
            report = f.collect(*args)
            download.assert_not_called()
        self.assertEqual(report["filings_parsed"],1)
        self.assertEqual(report["network_downloads"],0)
        self.assertFalse(report["live_execution_enabled"])
        self.assertFalse(report["approved_for_model_training"])

    def test_select_validation(self):
        with self.assertRaises(ValueError):
            f.select(["AAPL"],"2020-01-01","2020-01-02","split",20)
        with self.assertRaises(ValueError):
            f.select(["123"],"2020-01-01","2020-01-02","split",501)
