"""Offline V5.5 matching and request budget regression."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m_evidence_v55 as m
from atlaspm.data import hf_stock5m_evidence_v51 as ev

URL="https://www.sec.gov/Archives/edgar/data/123/0000000123-20-000001.txt"
FILING={"source_url":URL,"cik":"0000000123","form":"8-K","filed_date":"2020-02-04","sha256":"a"*64}
EVENT={"event_id":"event1","symbol":"ABC","date":"2020-02-03","kind":"OVERNIGHT_PRICE_GAP","price_ratio":24.5}


def body(text,cik="123"):
    return (f"<SEC-DOCUMENT>\n<SEC-HEADER>\nACCESSION NUMBER: 0000000123-20-000001\n"
            f"CENTRAL INDEX KEY: {cik}\n</SEC-HEADER>\n<DOCUMENT>\n<TYPE>8-K\n"
            f"<TEXT><p>{text}</p></TEXT></DOCUMENT></SEC-DOCUMENT>").encode()


TEXT="The 1-for-25 reverse stock split was effective on February 3, 2020. Trading symbol ABC."


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        old=Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir,old)
        Path("local_data").mkdir()

    def test_explicit_split_match_is_not_certification(self):
        r=m.match_split(EVENT,FILING,body(TEXT))
        self.assertEqual(r["status"],"MARKET_AND_TEXT_AGREE_REVIEW_REQUIRED")
        self.assertAlmostEqual(r["candidates"][0]["normalized_overnight_return_if_split"],-0.02)
        self.assertEqual(r["candidates"][0]["effective_date_mentions"],["2020-02-03"])
        self.assertEqual(r["ratio_terms"],["1/25"])

    def test_header_and_date_guards(self):
        self.assertEqual(m.match_split(EVENT,FILING,body(TEXT,"456"))["status"],
                         "ISSUER_OR_ACCESSION_HEADER_UNRESOLVED")
        self.assertEqual(m.match_split(EVENT,FILING,body(TEXT.replace("February 3","February 4")))["status"],
                         "SPLIT_TERMS_REQUIRE_REVIEW")
        self.assertEqual(m.match_split(EVENT,FILING,body("Proposed "+TEXT))["status"],
                         "SPLIT_TERMS_REQUIRE_REVIEW")

    def test_ambiguous_ratio_and_ticker(self):
        r=m.match_split(EVENT,FILING,body(TEXT+" Another 1-for-10 stock split."))
        self.assertEqual(r["status"],"AMBIGUOUS_RATIO_TERMS")
        r=m.match_split(EVENT,FILING,body(TEXT.replace("symbol ABC","symbol ABCD")))
        self.assertEqual(r["status"],"SPLIT_TERMS_REQUIRE_REVIEW")

    def test_raw_body_hash(self):
        data=body(TEXT)
        digest=ev._sha(data)
        f={**FILING,"sha256":digest}
        ev._write(m.filings.BASE/"bodies"/(digest+".bin"),data)
        ev._json(m.filings.BASE/"body_manifests"/(ev._sha(URL.encode())+".json"),
                 {"source_url":URL,"sha256":digest})
        actual,path=m._body(f)
        self.assertEqual(actual,data)
        path.write_bytes(b"altered")
        with self.assertRaises(ValueError):
            m._body(f)

    def test_external_reference_agreement_conflict(self):
        p=Path("local_data/exchange.txt")
        p.write_text("fixture exchange notice",encoding="utf-8")
        r={"symbol":"ABC","effective_date":"2020-02-03","new_over_old_shares":"1/25",
           "security_id":"ID1","source_family":"exchange","source_url":"https://example.org/notice",
           "evidence_path":str(p),"evidence_sha256":ev._sha(p.read_bytes()),"known_date":"2020-02-02"}
        source=Path("local_data/reference.jsonl")
        source.write_text(json.dumps(r),encoding="utf-8")
        refs=m.load_reference(source)
        q={"candidates":[EVENT]}
        inv={"results":[{**EVENT,"candidate_ciks":["0000000123"],"filings":[FILING]}]}
        with patch.object(m,"_body",return_value=(body(TEXT),p)):
            result=m.review(q,inv,refs)[0]
            self.assertEqual(result["status"],"EXTERNAL_REFERENCE_AGREES_REVIEW_REQUIRED")
            self.assertFalse(result["approved_for_ingest"])
            self.assertFalse(result["independent_source_verified"])
            refs[("ABC","2020-02-03")][0]["new_over_old_shares"]="1/20"
            self.assertEqual(m.review(q,inv,refs)[0]["status"],"EXTERNAL_REFERENCE_CONFLICT")
        p.write_text("changed",encoding="utf-8")
        with self.assertRaises(ValueError):
            m.load_reference(source)

    def test_batch_budget_and_checkpoints(self):
        q={"candidates":[],"total_detected":3,"candidate_count":0,"truncated":True}
        inv={"results":[],"requests_attempted":2,"network_downloads":2,"network_stopped":False,
             "unambiguous_current_leads":0}
        offline={**inv,"requests_attempted":0,"network_downloads":0}
        with patch.object(m.discovery,"scan",return_value=q), patch.object(m.discovery,"investigate",side_effect=[inv,offline]) as investigate:
            report=m.run("2020-01","2020-02",fetch=True,max_requests=2)
        self.assertEqual(report["months_completed"],2)
        self.assertEqual(report["requests_attempted"],2)
        self.assertFalse(investigate.call_args_list[1].kwargs["fetch"])
        self.assertTrue(Path(m.BASE/"months"/"2020-01.json").is_file())
        self.assertEqual(report["certified_events"],0)
        self.assertFalse(report["approved_for_model_training"])

    def test_failed_month_does_not_stop_offline_remaining(self):
        q={"candidates":[],"total_detected":0,"candidate_count":0,"truncated":False}
        inv={"results":[],"requests_attempted":0,"network_downloads":0,"network_stopped":False,
             "unambiguous_current_leads":0}
        with patch.object(m.discovery,"scan",side_effect=[ValueError("missing"),q]), patch.object(m.discovery,"investigate",return_value=inv) as investigate:
            report=m.run("2020-01","2020-02",fetch=True)
        self.assertEqual(report["months_completed"],1)
        self.assertTrue(report["network_stopped"])
        self.assertFalse(investigate.call_args.kwargs["fetch"])
