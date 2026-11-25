"""Targeted evidence planning is bounded and never authorizes identities."""
from datetime import date
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atlaspm.research import evidence_acquisition as a
from atlaspm.data import hf_stock5m_identity_market_review as market


def row(symbol, gap=10, months=None):
    return {"symbol":symbol,"identity_gap_horizon_rows":gap,
            "visible_action_excluded_horizon_rows":0,"feature_history_gap_horizon_rows":0,
            "affected_signal_months":months or ["2000-03"]}


def ref(symbol,cik,key,**kw):
    return dict({"symbol":symbol,"cik":cik,"reference_id":key,"known_date":"2000-03-01",
                 "valid_from":"2000-03-01","valid_to":"2000-04-01"},**kw)


class TargetedPlanTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        old=Path.cwd()
        os.chdir(tmp.name)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir,old)
        Path("local_data").mkdir()
        self.path=Path("local_data/priority.json")

    def write(self,rows):
        self.path.write_text(json.dumps({"schema":a.PREFLIGHT_SCHEMA,"dataset_id":"a"*64,
            "approved_for_model_training":False,"evidence_priority":{
                "schema":"atlaspm_evidence_priority_v1","all_counts_reconciled":True,
                "claims_ingested":0,"reported_symbols":len(rows),"truncated":False,
                "priority_rows":rows}}),encoding="utf-8")

    def test_no_reference_means_no_issuer_guess(self):
        self.write([row("ABC")])
        r=a.plan(self.path,year=2000,as_of="2001-01-01")
        self.assertEqual(r["selected_ciks"],[])
        self.assertEqual(r["tasks"][0]["status"],"EXTERNAL_EXPLICIT_CIK_REQUIRED")
        self.assertEqual(r["network_requests"],0)
        self.assertFalse(r["approved_for_model_training"])

    def test_budget_never_splits_conflicting_symbol(self):
        self.write([row("LOW",1),row("HIGH",100),row("MID",10)])
        refs=[ref("HIGH","0000000001","r1"),ref("MID","0000000002","r2"),
              ref("MID","0000000003","r3"),ref("LOW","0000000001","r4")]
        with patch.object(a.reference,"load_reference",return_value=(refs,{"sha256":"a"*64})):
            r=a.plan(self.path,year=2000,as_of="2001-01-01",reference_file="dummy",max_issuers=2)
        self.assertEqual(r["selected_ciks"],["0000000001"])
        self.assertEqual([x["symbol"] for x in r["tasks"]],["HIGH","MID","LOW"])
        self.assertEqual(r["tasks"][1]["status"],"ISSUER_BUDGET_DEFERRED")
        self.assertEqual(r["tasks"][1]["declared_cik_candidates"],["0000000002","0000000003"])

    def test_cutoff_exclusive_interval_and_nonidentity_gap(self):
        self.write([row("A"),row("B"),row("C",0),row("D",months=["2001-01"])])
        refs=[ref("A","0000000001","r1",known_date="2000-04-01"),
              ref("B","0000000002","r2",valid_from="2000-02-01",valid_to="2000-03-01"),
              ref("C","0000000003","r3")]
        with patch.object(a.reference,"load_reference",return_value=(refs,{})):
            r=a.plan(self.path,year=2000,as_of="2000-04-01",reference_file="dummy")
        self.assertEqual(r["selected_ciks"],[])
        self.assertEqual([x["status"] for x in r["tasks"]],
                         ["EXTERNAL_EXPLICIT_CIK_REQUIRED","EXTERNAL_EXPLICIT_CIK_REQUIRED",
                          "NON_IDENTITY_GAP_REVIEW"])
        self.assertEqual(a._month_span("2000-12"),("2000-12-01",date(2001,1,1).isoformat()))

    def test_invalid_queue_fails_before_reference_load(self):
        for rows in ([row("A"),row("A")],[row("A",-1)],[row("A",months=["bad"])]):
            self.write(rows)
            with patch.object(a.reference,"load_reference") as load:
                with self.assertRaises(ValueError):
                    a.plan(self.path,year=2000,as_of="2001-01-01",reference_file="dummy")
                load.assert_not_called()

    def test_targeted_market_review_reuses_cache_only_collector(self):
        page={"results":[],"report_path":"local_data/page.json","index_partition_sha256":"a"*64,
              "page_complete":True,"page_advance_allowed":True,"resume_after_cik":None,
              "requested_ciks":["0000000001","0000000002"],"selected_issuers":["0000000001"],
              "filings_with_bodies":0}
        with patch.object(market.identity,"run",return_value=page) as run,patch.object(market.ev,"_json"):
            r=market.run(2000,"2000-03","2000-03",ciks=["1","2"])
        self.assertEqual(run.call_args.kwargs["ciks"],["1","2"])
        self.assertFalse(run.call_args.kwargs["fetch"])
        self.assertEqual(r["requested_ciks_not_selected"],["0000000002"])
        with self.assertRaises(ValueError):
            market.run(2000,"2000-03","2000-03",ciks=["1"],after_cik="2")
