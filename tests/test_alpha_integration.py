"""Regression tests for financial facts and whole-universe candidate aggregation."""
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from atlaspm.research.alpha_fundamentals import extract, ratios
from atlaspm.research.alpha_scan import scan
from atlaspm.research import event_queue


class FundamentalTests(unittest.TestCase):
    def test_filing_cutoff_and_period_ratios(self):
        def row(value, **extra):
            return dict(start="2024-01-01",end="2024-12-31",filed="2025-02-01",
                        form="10-K",accn="0000000001-25-000001",val=value,**extra)
        tags={"Revenues":[row(100)],"NetIncomeLoss":[row(20)],
              "NetCashProvidedByUsedInOperatingActivities":[row(30)],
              "PaymentsToAcquirePropertyPlantAndEquipment":[row(10)]}
        tags["NetIncomeLoss"].append(dict(row(999),filed="2025-02-02"))
        data={"cik":1,"facts":{"us-gaap":{
            k:{"units":{"USD":v}} for k,v in tags.items()}}}
        facts=extract(data,cik="0000000001",as_of="2025-02-02T12:00:00+00:00")
        self.assertEqual(facts["NetIncomeLoss"]["annual"]["value"],20)
        result=ratios(facts)["annual"]
        self.assertAlmostEqual(result["net_margin"],.2)
        self.assertEqual(result["free_cash_flow"],20)
        self.assertEqual(result["operating_cash_flow_to_net_income"],1.5)
        facts["Revenues"]["annual"]["accession"]="different"
        self.assertNotIn("net_margin",ratios(facts)["annual"])

    def test_ambiguous_fact_rejected(self):
        a=dict(start="2024-01-01",end="2024-12-31",filed="2025-02-01",
               form="10-K",accn="a",val=10)
        data={"cik":1,"facts":{"us-gaap":{"Revenues":{"units":{
            "USD":[a,dict(a,val=11)]}}}}}
        with self.assertRaises(ValueError):
            extract(data,cik="0000000001",as_of="2025-02-02T00:00:00+00:00")


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.universe={"rows":{s:{"active":True} for s in ("AAA","BBB","CCC")},
                       "sha256":"fixture"}
    def candidate(self,s,seconds=60):
        return {"symbol":s,"action":"LONG","candidate_only":True,
                "expires_at":(datetime.now(UTC)+timedelta(seconds=seconds)).isoformat()}
    @patch.object(event_queue,"_read_state",return_value={})
    def test_aggregation_expiry_and_partial(self,_):
        def worker(*,universe,model,path):
            s=universe[0]
            if s=="BBB":
                raise ValueError("fixture")
            return {"status":"CANDIDATES","opportunities":[self.candidate(s,60 if s=="AAA" else -1)]}
        report=scan(self.universe,model="fixture",batch_size=1,worker=worker)
        self.assertEqual(report["status"],"PARTIAL")
        self.assertEqual(report["batches_completed"],2)
        self.assertEqual([x["symbol"] for x in report["opportunities"]],["AAA"])
    @patch.object(event_queue,"_read_state",return_value={})
    def test_reject_cross_batch_candidate(self,_):
        def worker(**kwargs):
            return {"status":"CANDIDATES","opportunities":[self.candidate("ZZZ")]}
        report=scan(self.universe,model="fixture",batch_size=1,worker=worker)
        self.assertEqual(report["batches_failed"],3)
        self.assertEqual(report["opportunities"],[])
    @patch.object(event_queue,"_read_state",return_value={event_queue.CONTROL_KEY:{"stopped":True}})
    def test_pause(self,_):
        def worker(**kwargs):
            self.fail("Inference must not run while paused")
        self.assertEqual(scan(self.universe,model="fixture",worker=worker)["status"],"PAUSED")
