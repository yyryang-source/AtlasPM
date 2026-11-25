"""Cross-event current research visibility, revision and rights tests."""
from datetime import UTC,datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import unittest

from atlaspm.research import current_research as current,event_queue as queue
from atlaspm.research.financial_events import normalize
from atlaspm.research.research_contract import CATEGORIES
from atlaspm.research.training_registry import digest


class CurrentResearchTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        prior=Path.cwd()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir,prior)
        self.now=datetime.now(UTC)-timedelta(seconds=2)
        self.sid="CIK:0000000001"
        self.items={}

    def stamp(self,seconds):
        return (self.now+timedelta(seconds=seconds)).isoformat()

    def add(self,source,category,*,receipt=-100,completed=-80,asset="stock",state="completed",ttl=3600,route="sec_local"):
        event=normalize({"provider":"sec","source_id":source,"kind":"sec_disclosure",
            "subjects":["ticker:AAA"],"published_at":self.stamp(receipt),
            "first_available_at":self.stamp(receipt),"effective_at":None,
            "evidence_sha256":digest([source,receipt]),"delivery":"file","expiry_seconds":ttl},
            received_at=self.stamp(receipt))
        rid=digest([source,category,asset])
        decision={"status":"DESCRIPTIVE_CONTRACT_DECISION_REVIEWED","research_id":rid,
                  "category":category,"output_sha256":"b"*64,"decision_sha256":"c"*64,
                  "numeric_eligible":False,"identity_independently_verified":False,
                  "trade_authorized":False,"action":"NO_TRADE"}
        dossier={"security_id":self.sid,"asset_type":asset,"as_of":self.stamp(receipt+1),
            "coverage":{k:"descriptive_reviewed" if k==category else "missing" for k in CATEGORIES},
            "research_ids":[rid],"decision_sha256":"d"*64,"action":"NO_TRADE","numeric_count":0,
            "trade_authorized":False,"identity_independently_verified":False}
        report={"as_of":self.stamp(receipt+1),"review_completed_at":self.stamp(completed),
                "joint_specialist_dossiers":[dossier],"local_specialist_receipts":[{
                    "task_sha256":"e"*64,"report_sha256":"f"*64,"route":route,
                    "status":"LOCAL_REVIEW_COMPLETED","security_id":self.sid,
                    "event_subject":"ticker:AAA","decision_receipt":decision}]}
        key=digest([event["event_id"],event["revision"]])
        self.items[key]={"event":event,"state":state,"stored_at":self.stamp(receipt),
                         "attempts":1,"report":report if state=="completed" else None}
        return self.items[key]

    def run_view(self,**kwargs):
        queue.PATH.parent.mkdir(parents=True,exist_ok=True)
        queue.PATH.write_text(json.dumps({"items":self.items,"sha256":digest(self.items)}),encoding="utf-8")
        before=queue.PATH.read_bytes()
        out=current.inspect(as_of=self.now.isoformat(),security_ids=[self.sid],**kwargs)
        self.assertEqual(queue.PATH.read_bytes(),before)
        self.assertFalse(out["trade_authorized"])
        return out

    def test_two_events_one_current_coverage_and_no_trade(self):
        self.add("one","sec")
        self.add("two","fundamental",route="fundamental_local")
        out=self.run_view()
        d=out["securities"][0]
        self.assertEqual(d["category_evidence_counts"],{"sec":1,"fundamental":1})
        self.assertEqual(d["action"],"NO_TRADE")
        self.assertFalse(d["identity_independently_verified"])

    def test_new_pending_revision_suppresses_previous_review(self):
        self.add("one","sec",receipt=-200,completed=-180)
        self.add("one","sec",receipt=-50,state="pending")
        out=self.run_view()
        self.assertEqual(out["securities"][0]["status"],"NO_CURRENT_RESEARCH")
        self.assertEqual(out["excluded"]["superseded_visible_revisions"],1)
        self.assertEqual(out["excluded"]["latest_revision_not_completed"],1)

    def test_time_unknown_future_and_stale_do_not_count(self):
        self.add("legacy","sec").pop("stored_at")
        self.add("legacy-review","sec")["report"].pop("review_completed_at")
        self.add("late","fundamental",completed=1)
        self.add("expired","bond",ttl=10)
        out=self.run_view()
        self.assertEqual(out["securities"][0]["receipts"],[])
        for reason in ("legacy_storage_time_unknown","legacy_completion_time_unknown",
                       "review_not_yet_visible","expired_event_or_review"):
            self.assertEqual(out["excluded"][reason],1)

    def test_conflicting_asset_type_blocks_union(self):
        self.add("one","sec")
        self.add("two","bond",asset="bond",route="bond_local")
        out=self.run_view()
        self.assertEqual(out["securities"][0]["status"],"CONFLICTING_ASSET_ASSOCIATIONS")
        self.assertEqual(out["securities"][0]["receipts"],[])

    def test_unmatched_receipt_rejected(self):
        a=self.add("one","sec")
        a["report"]["local_specialist_receipts"][0]["decision_receipt"]["research_id"]="a"*64
        with self.assertRaisesRegex(ValueError,"RECEIPT_CONTRACT"):
            self.run_view()

    def test_revoked_specialist_rights_exclude_only_that_receipt(self):
        self.add("one","sec")
        self.add("two","news",route="bloomberg")
        with patch.object(current,"local_rights",side_effect=ValueError("rights")):
            out=self.run_view()
        self.assertEqual(out["securities"][0]["category_evidence_counts"],{"sec":1})
        self.assertEqual(out["excluded"]["specialist_rights_unconfirmed"],1)
