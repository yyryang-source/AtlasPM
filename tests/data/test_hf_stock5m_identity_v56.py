"""Offline historical cover observation tests; no SEC requests."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m_identity_v56 as v
from atlaspm.data import hf_stock5m_filings_v52 as f


def filing(cik="0000000123",day="2020-01-03",number="000001"):
    return {"quarter":"2020-Q1","cik":cik,"issuer":"Example","form":"8-K",
            "filed_date":day,"source_url":f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/0000000123-20-{number}.txt"}


def row(symbol="ABC",context="c1",exchange="The Nasdaq Stock Market LLC",title="Common Stock",exchange_context=None):
    fields=[("Security12bTitle",title,context),("TradingSymbol",symbol,context),
            ("SecurityExchangeName",exchange,exchange_context or context)]
    return "<tr>"+"".join(f'<td><ix:nonNumeric name="dei:{k}" contextRef="{c}">{val}</ix:nonNumeric></td>'
                            for k,val,c in fields)+"</tr>"


def body(item,html,extra=""):
    accession=item["source_url"].rsplit("/",1)[-1][:-4]
    return (f'<SEC-DOCUMENT>\n<SEC-HEADER>\nACCESSION NUMBER: {accession}\n'
            f'CENTRAL INDEX KEY: {item["cik"]}\nFILED AS OF DATE: {item["filed_date"].replace("-","")}\n'
            f'</SEC-HEADER>\n<DOCUMENT>\n<TYPE>{item["form"]}\n<TEXT><table>{html}</table></TEXT></DOCUMENT>'
            +extra+"</SEC-DOCUMENT>").encode()


class ExtractTests(unittest.TestCase):
    def test_share_classes_same_row(self):
        x=filing()
        r=v.extract(body(x,row()+row("ABC.B","c2","NYSE","Class B")),x)
        self.assertEqual([(o["symbol"],o["exchange_mic_candidate"]) for o in r["observations"]],
                         [("ABC","XNAS"),("ABC.B","XNYS")])
        self.assertTrue(all(o["security_id"] is None and o["valid_from"] is None for o in r["observations"]))

    def test_cross_context_and_unsupported_exchange_refused(self):
        x=filing()
        r=v.extract(body(x,row(exchange_context="other")+row("OTC","c2","OTC")),x)
        self.assertEqual(r["observations"],[])
        self.assertEqual(r["rejected_tagged_rows"],2)

    def test_conflict_does_not_pick_one(self):
        x=filing()
        r=v.extract(body(x,row()+row(context="c2",exchange="NYSE")),x)
        self.assertEqual(r["observations"],[])
        self.assertEqual(r["conflicting_symbols"],["ABC"])

    def test_legacy_explicit_columns_only(self):
        x=filing()
        header="<tr><th>Title of each class</th><th>Trading Symbol(s)</th><th>Name of each exchange on which registered</th></tr>"
        legacy="<tr><td>Common stock</td><td>ABC</td><td>New York Stock Exchange</td></tr>"
        r=v.extract(body(x,header+legacy),x)
        self.assertEqual(r["observations"][0]["method"],"EXPLICIT_LEGACY_TABLE")
        self.assertEqual(r["observations"][0]["exchange_mic_candidate"],"XNYS")
        self.assertIsNone(r["observations"][0]["valid_from"])
        for html in (legacy,header+legacy.replace("<td>ABC</td>",""),
                     header+legacy.replace("<td>ABC","<td colspan='2'>ABC"),
                     header+legacy+row(exchange_context="other")):
            self.assertEqual(v.extract(body(x,html),x)["observations"],[])


    def test_periodic_legacy_listing_is_text_lead_not_cover_identity(self):
        x={**filing(),"form":"10-K405"}
        exact='<p>Our common stock is traded on the Nasdaq National Market under the symbol "ABC".</p>'
        result=v.extract(body(x,exact),x)
        self.assertEqual(result["observations"],[])
        self.assertEqual(result["status"],"NO_SUPPORTED_COVER_FACTS")
        lead=result["legacy_listing_text_leads"][0]
        self.assertEqual(lead["symbol_candidate"],"ABC")
        self.assertEqual(lead["status"],"UNVERIFIED_LEGACY_LISTING_TEXT")
        self.assertIsNone(lead["security_id"])
        self.assertIsNone(lead["valid_from"])
        for text in (exact.replace('"ABC"',"ABC"),
                     exact.replace("Our common stock","Their common stock"),
                     exact.replace('"ABC"','"abc"'),
                     exact+row(exchange_context="other")):
            self.assertEqual(v.extract(body(x,text),x)["legacy_listing_text_leads"],[])
        exhibit="<DOCUMENT>\n<TYPE>EX-99\n<TEXT>"+exact+"</TEXT></DOCUMENT>"
        self.assertEqual(v.extract(body(x,"",extra=exhibit),x)["legacy_listing_text_leads"],[])

    def test_header_and_exhibits(self):
        x=filing()
        b=body(x,"",extra="<DOCUMENT>\n<TYPE>EX-99\n<TEXT><table>"+row()+"</table></TEXT></DOCUMENT>")
        self.assertEqual(v.extract(b,x)["status"],"NO_SUPPORTED_COVER_FACTS")
        for altered in (b.replace(b"20200103",b"20200104"),b.replace(b"CENTRAL INDEX KEY: 0000000123",b"CENTRAL INDEX KEY: 0000000124")):
            with self.assertRaises(ValueError):
                v.extract(altered,x)


class BatchTests(unittest.TestCase):
    def setUp(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("lake extra required")
        self.tmp=TemporaryDirectory()
        previous=Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir,previous)
        Path("local_data").mkdir()
        self.items=[filing(),filing(day="2020-02-03",number="000002"),
                    filing(cik="0000000124",number="000003")]
        cx=f._db()
        try:
            cx.execute("INSERT INTO partitions VALUES ('2020-Q1',?,'v52.1',3,0)",["a"*64])
            cx.executemany("INSERT INTO filings VALUES (?,?,?,?,?,?)",
                           [[x[k] for k in ("quarter","cik","issuer","form","filed_date","source_url")] for x in self.items])
        finally:
            cx.close()

    def test_historical_pagination(self):
        p=v.plan(2020,max_issuers=1)
        self.assertEqual(p["filings"],[self.items[1]])
        self.assertEqual(p["next_after_cik"],"0000000123")
        self.assertEqual(p["missing_index_quarters"],["2020-Q2","2020-Q3","2020-Q4"])
        p=v.plan(2020,max_issuers=1,after_cik=p["next_after_cik"])
        self.assertEqual(p["filings"],[self.items[2]])

    def test_exit_only_issuer_and_incomplete_index(self):
        x={**filing(cik="0000000125",number="000004"),"form":"25-NSE"}
        cx=f._db()
        try:
            cx.execute("INSERT INTO filings VALUES (?,?,?,?,?,?)",
                       [x[k] for k in ("quarter","cik","issuer","form","filed_date","source_url")])
        finally:
            cx.close()
        r=v.run(2020,ciks=["125"])
        self.assertEqual(r["selected_issuers"],["0000000125"])
        self.assertEqual(r["issuers_without_sampled_cover_filing"],["0000000125"])
        self.assertEqual(r["filings_selected"],0)
        self.assertEqual(r["event_index_leads"][0]["kind"],"EXIT_FILING_LEAD")
        self.assertIsNone(r["event_index_leads"][0]["effective_date"])
        self.assertFalse(r["page_advance_allowed"])
        self.assertEqual(r["certified_identities"],0)

    def test_budget_resume_cache_and_no_certification(self):
        with patch.object(v.ev,"download",side_effect=[body(self.items[1],row()),body(self.items[2],row("XYZ"))]) as fetch, \
             patch.object(v.time,"sleep"),patch.dict(os.environ,{"ATLASPM_SEC_USER_AGENT":"Test test@example.com"}):
            a=v.run(2020,fetch=True,max_requests=1)
            self.assertFalse(a["page_complete"])
            self.assertEqual(a["cover_observations"],1)
            b=v.run(2020,fetch=True,max_requests=1)
            self.assertTrue(b["page_complete"])
            self.assertEqual(b["cover_observations"],2)
            c=v.run(2020)
            self.assertEqual(c["cover_observations"],2)
            self.assertEqual(c["network_downloads"],0)
            self.assertEqual(fetch.call_count,2)
        self.assertEqual(c["certified_identities"],0)
        self.assertEqual(c["claims_ingested"],0)
        self.assertFalse(c["approved_for_model_training"])
        self.assertFalse(c["live_execution_enabled"])

    def test_network_failure_stops_provider(self):
        with patch.object(v.ev,"download",side_effect=ValueError("Provider HTTP 429")) as fetch, \
             patch.object(v.time,"sleep"),patch.dict(os.environ,{"ATLASPM_SEC_USER_AGENT":"Test test@example.com"}):
            r=v.run(2020,fetch=True)
        self.assertTrue(r["network_stopped"])
        self.assertFalse(r["page_complete"])
        self.assertEqual(fetch.call_count,1)


    def test_periodic_annual_priority_one_body_per_issuer_and_rebuild(self):
        annual={**filing(day="2020-03-20",number="000010"),"form":"10-K405"}
        quarterly={**filing(day="2020-01-15",number="000011"),"form":"10-Q"}
        other={**filing(cik="0000000124",day="2020-02-20",number="000012"),"form":"10-QSB"}
        cx=f._db()
        try:
            cx.executemany("INSERT INTO filings VALUES (?,?,?,?,?,?)",
                [[x[k] for k in ("quarter","cik","issuer","form","filed_date","source_url")]
                 for x in (annual,quarterly,other)])
        finally:
            cx.close()
        with patch.object(v.ev,"download") as download:
            with self.assertRaisesRegex(ValueError,"PERIODIC_INDEX_REBUILD_REQUIRED"):
                v.run(2020,periodic_first=True)
            download.assert_not_called()
        cx=f._db()
        try:
            cx.execute("UPDATE partitions SET parser_version=?,candidate_rows=6",[f.VERSION])
            for q in ("2020-Q2","2020-Q3","2020-Q4"):
                cx.execute("INSERT INTO partitions VALUES (?,?,?,?,0)",[q,"a"*64,f.VERSION,0])
        finally:
            cx.close()
        plan=v.plan(2020,periodic_first=True)
        self.assertEqual(plan["filings"],[annual,other])
        self.assertEqual(plan["stale_periodic_index_quarters"],[])
        self.assertEqual(v.plan(2020,max_issuers=1)["filings"],[self.items[1]])
        with patch.object(v.ev,"download") as download:
            result=v.run(2020,periodic_first=True)
        download.assert_not_called()
        self.assertEqual(result["filings_selected"],2)
        self.assertFalse(result["page_complete"])
        self.assertEqual(result["certified_identities"],0)
        self.assertEqual(result["claims_ingested"],0)
        self.assertFalse(result["approved_for_model_training"])
