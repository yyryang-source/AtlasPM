"""Event-scoped joint descriptive research using existing contracts and queue."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import os

from atlaspm.research import intelligence as intel,event_queue
from atlaspm.research.training_registry import digest
from atlaspm.research.financial_events import normalize
from atlaspm.framework.orchestrator import status

STAMP="2020-01-10T21:00:00+00:00"


def bindings():
    return [
        {"kind":"sec_disclosure","subject":"ticker:AAA","identity":{"security_id":"CIK:0000000001",
            "provider_id":"0000000001","asset_type":"stock"},
         "task":{"route":"sec_local","params":{"start":"2019-01-01","end":"2020-01-09","symbols":["0000000001"]}}},
        {"kind":"sec_disclosure","subject":"ticker:AAA","identity":{"security_id":"CIK:0000000001",
            "provider_id":"AAA","asset_type":"stock"},
         "task":{"route":"fundamental_local","params":{"start":"2019-01-01","end":"2020-01-09","symbols":["AAA"],"path":"local_data/f.csv"}}}
    ]


def local_report(route,args,cutoff):
    # Normalized specialist fixture: not source data or identity proof.
    if route=="sec_local":
        return {"research_only":True,"subject_row_counts":{"0000000001":2}}
    return {"research_only":True,"fundamental_screen":{"ranked":[{"symbol":"AAA"}],
                                                       "method":"fixture_fundamental"}}


class JointSpecialistTests(unittest.TestCase):
    def results(self,b):
        with patch.object(intel,"_local_data_review",side_effect=local_report):
            return intel.local_specialist_tasks([x["task"] for x in b],as_of=STAMP)

    def test_joined_coverage_digests_and_no_numeric_authority(self):
        b=bindings()
        r=intel.joint_specialist_review(b,self.results(b),as_of=STAMP)
        d=r["dossiers"][0]
        self.assertEqual(d["security_id"],"CIK:0000000001")
        self.assertEqual(len(d["coverage"]),15)
        self.assertEqual({k for k,v in d["coverage"].items() if v=="descriptive_reviewed"},{"sec","fundamental"})
        self.assertEqual(len(d["research_ids"]),2)
        self.assertEqual(d["action"],"NO_TRADE")
        self.assertEqual(d["numeric_count"],0)
        self.assertFalse(d["trade_authorized"])
        self.assertTrue(all("report" not in x and "_contract" not in x["decision_receipt"] for x in r["receipts"]))

    def test_report_mutation_and_unmatched_subject_never_count(self):
        b=bindings()
        result=self.results(b)
        result["tasks"][0]["report"]["subject_row_counts"]["0000000001"]=999
        result["tasks"][1]["report"]["fundamental_screen"]["ranked"]=[{"symbol":"OTHER"}]
        result["tasks"][1]["report_sha256"]=digest(result["tasks"][1]["report"])
        r=intel.joint_specialist_review(b,result,as_of=STAMP)
        self.assertEqual(r["dossiers"][0]["research_ids"],[])
        self.assertEqual(r["dossiers"][0]["status"],"NO_ADMISSIBLE_SPECIALIST_OUTPUT")
        self.assertTrue(all(v=="missing" for v in r["dossiers"][0]["coverage"].values()))

    def test_mixed_identity_and_fixture_mapping_rejected(self):
        for target in ("CIK:0000000002","SYN:AAA"):
            b=bindings()
            b[1]["identity"]["security_id"]=target
            with self.assertRaises(ValueError):
                intel.validate_local_bindings(b)
        b=bindings()
        b[1]["identity"]["asset_type"]="bond"
        with self.assertRaisesRegex(ValueError,"CONFLICTING"):
            intel.validate_local_bindings(b)
        b=bindings()
        b[0]["subject"]="SYN:AAA"
        with self.assertRaises(ValueError):
            intel.validate_local_bindings(b)

    def test_task_alignment_and_cutoff(self):
        b=bindings()
        for edit in (lambda r:r["tasks"].pop(),
                     lambda r:r.update(as_of="2020-01-11T21:00:00+00:00"),
                     lambda r:r.update(network_calls=1)):
            r=self.results(b)
            edit(r)
            with self.assertRaises(ValueError):
                intel.joint_specialist_review(b,r,as_of=STAMP)

    def test_event_queue_persists_only_joint_receipts(self):
        with TemporaryDirectory() as tmp:
            prior=Path.cwd()
            os.chdir(tmp)
            try:
                with patch("atlaspm.research.financial_events.require_enabled"), \
                     patch("atlaspm.data.provider_policy.local_rights"), \
                     patch("atlaspm.research.financial_events.local_rights"), \
                     patch.object(intel,"_local_data_review",side_effect=local_report):
                    event=normalize({"provider":"sec","source_id":"fixture","kind":"sec_disclosure",
                        "subjects":["ticker:AAA"],"published_at":STAMP,"first_available_at":STAMP,
                        "effective_at":None,"evidence_sha256":"a"*64,"delivery":"file","expiry_seconds":3600},
                        received_at=STAMP)
                    event_queue.enqueue([event])
                    r=event_queue.drain(as_of=STAMP,local_bindings=bindings())
                    self.assertEqual(r["completed"],1)
                    self.assertEqual(r["local_research_calls"],2)
                    saved=next(iter(event_queue._read_state(event_queue.PATH).values()))["report"]
                    self.assertEqual(len(saved["joint_specialist_dossiers"]),1)
                    self.assertEqual(status()["joint_specialist_dossier_count"],1)
                    self.assertEqual(status()["joint_dossier_descriptive_category_counts"],{"sec":1,"fundamental":1})
                    self.assertEqual(event_queue.drain(as_of=STAMP,local_bindings=bindings())["local_research_calls"],0)
            finally:
                os.chdir(prior)
