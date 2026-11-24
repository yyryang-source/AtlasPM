"""Unified research and event-to-flat tests; no network, broker or real data."""
from datetime import UTC,datetime,timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import os
import unittest

from atlaspm.research.research_contract import contract
from atlaspm.research.signal_fusion import fuse
from atlaspm.engine.autonomous_decision import decide
from atlaspm.framework.autonomous_cycle import run_fixture
from atlaspm.research.financial_events import normalize
from atlaspm.research import event_queue

ROOT=Path(__file__).resolve().parents[2]
NOW="2026-11-20T21:00:00Z"


def signal(model="m1",value=.1,ref="a",**changes):
    row={"security_id":"SYN:AAA","asset_type":"stock","category":"factor",
         "kind":"quantitative_prediction","source":"synthetic","model_version":model,
         "evidence":[{"sha256":ref*64,"available_at":"2026-11-20T20:00:00Z",
                      "received_at":"2026-11-20T20:01:00Z","source":"synthetic",
                      "rights_status":"synthetic_test_only"}],
         "generated_at":"2026-11-20T20:30:00Z","expires_at":"2026-11-21T00:00:00Z",
         "horizon_days":365,"expected_return":value,"uncertainty_radius":.02,
         "risk_factors":["fixture"],"invalidation":["expiry"],"synthetic_fixture":True}
    row.update(changes)
    return row


class UnifiedResearchTests(unittest.TestCase):
    def test_contract_time_and_qualitative_numeric_boundary(self):
        self.assertTrue(contract(signal(),as_of=NOW)["numeric_eligible"])
        for row in (signal(kind="llm_inference"),signal(generated_at="2026-11-21T00:00:00Z"),
                    signal(approved_for_model_training=True),signal(security_id="AAPL")):
            with self.assertRaises(ValueError):
                contract(row,as_of=NOW)
        row=signal()
        row["evidence"][0]["available_at"]=None
        self.assertFalse(contract(row,as_of=NOW)["numeric_eligible"])

    def test_fusion_weights_envelope_and_no_permission(self):
        out=fuse([signal(),signal("m2",.12,"b")],as_of=NOW,weights={"m1":3,"m2":1})
        g=out["groups"][0]
        self.assertAlmostEqual(g["expected_return"],.105)
        self.assertAlmostEqual(g["lower_bound"],.08)
        self.assertAlmostEqual(g["upper_bound"],.14)
        self.assertTrue(g["synthetic_decision_eligible"])
        self.assertFalse(g["statistically_calibrated"])
        self.assertFalse(out["approved_for_model_training"])

    def test_overlap_disagreement_and_horizon_not_pooled(self):
        for rows in ([signal(),signal("m2",.12,"a")],
                     [signal(),signal("m2",-.1,"b")],
                     [signal(),signal("m2",.12,"b",horizon_days=30)]):
            result=fuse(rows,as_of=NOW,weights={"m1":1,"m2":1})
            self.assertTrue(all(not g["synthetic_decision_eligible"] for g in result["groups"]))
        with self.assertRaises(ValueError):
            fuse([signal(),signal()],as_of=NOW,weights={"m1":1})

    def test_buy_sell_hold_no_trade(self):
        for vals,action in (((.1,.12),"BUY"),((-.1,-.12),"SELL"),((.02,.025),"HOLD"),((.1,-.1),"NO_TRADE")):
            result=decide([signal(value=vals[0]),signal("m2",vals[1],"b")],
                          as_of=NOW,weights={"m1":2,"m2":1},horizon_days=365,cost_fraction=.03)
            self.assertEqual(result["candidates"][0]["action"],action)
            self.assertFalse(result["trade_authorized"])

    @unittest.skipUnless(importlib.util.find_spec("scipy"),"portfolio extra required")
    def test_full_fixture_existing_oms_to_flat(self):
        p=json.loads((ROOT/"examples/unified_portfolio_synthetic.json").read_text())
        result=run_fixture(p)
        self.assertEqual(result["decision"],"SYNTHETIC_EVENT_TO_FLAT_COMPLETE",result.get("reason",result.get("entry",{}).get("reason")))
        self.assertTrue(result["attribution"]["reconciled"])
        self.assertFalse(result["broker_side_effects"])
        self.assertEqual(result["exit_oms"]["account_replay"]["unsettled_receivable"],0)
        self.assertTrue(all(o["status"]=="FILLED" for o in result["exit_oms"]["orders"]))

    def test_risk_gate_blocks_or_permission_rejected(self):
        p=json.loads((ROOT/"examples/unified_portfolio_synthetic.json").read_text())
        p["live_execution_enabled"]=True
        self.assertEqual(run_fixture(p)["decision"],"ENTRY_BLOCKED")
        p["live_execution_enabled"]=False
        p["constraints"]["max_stress_loss_fraction"]=.01
        self.assertEqual(run_fixture(p)["decision"],"ENTRY_BLOCKED")

    def test_queue_dedup_priority_resume_and_rights(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cwd=Path.cwd()
        os.chdir(temp.name)
        self.addCleanup(os.chdir,cwd)
        Path("local_data").mkdir()
        now=datetime.now(UTC)
        def event(kind,source_id):
            return normalize({"provider":"sec","source_id":source_id,"kind":kind,
                "subjects":["CIK:0000320193"],"published_at":(now-timedelta(minutes=1)).isoformat(),
                "first_available_at":None,"effective_at":None,"evidence_sha256":"a"*64,
                "delivery":"poll","expiry_seconds":3600},received_at=now.isoformat())
        a,b=event("sec_disclosure","a"),event("earnings","b")
        self.assertEqual(event_queue.enqueue([a,b,a])["added"],2)
        self.assertEqual(event_queue.drain(as_of=now.isoformat(),limit=1)["pending"],1)
        self.assertEqual(event_queue.enqueue([a])["added"],0)
        self.assertEqual(event_queue.drain(as_of=now.isoformat(),limit=1)["pending"],0)
        self.assertEqual(event_queue.drain(as_of=now.isoformat())["completed"],0)
        state=json.loads(event_queue.PATH.read_text())
        state["items"].clear()
        event_queue.PATH.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError,"INTEGRITY"):
            event_queue.drain(as_of=now.isoformat())


    def test_dispatch_preflights_budget_and_never_auto_calls_external(self):
        from atlaspm.research.intelligence import dispatch_tasks
        from unittest.mock import patch
        task={"kind":"public","provider":"openai","model":"fixture-model","topic":"analyst",
              "as_of":NOW,"max_tokens":128,"evidence":[{"evidence_id":"fixture",
              "source":"public fixture","available_at":"2026-11-20T20:00:00Z",
              "text":"fixture","external_use_reviewed":True}]}
        with patch.dict(os.environ,{"ATLASPM_ALLOW_EXTERNAL_AI":"1","OPENAI_API_KEY":"fixture"}):
            with patch("atlaspm.research.intelligence.research_public") as call:
                with self.assertRaisesRegex(ValueError,"BUDGET"):
                    dispatch_tasks([task],external_output_budget=0,allow_external=True)
                call.assert_not_called()
                call.return_value={"research_only":True}
                result=dispatch_tasks([task,task],external_output_budget=256,allow_external=True)
                self.assertEqual(result["unique_tasks"],1)
                call.assert_called_once()
                self.assertFalse(result["broker_order_submission_enabled"])

    def test_historical_diagnostics_reject_real_security_stream(self):
        from atlaspm.research.signal_fusion import historical_diagnostics
        with self.assertRaisesRegex(ValueError,"SYNTHETIC"):
            historical_diagnostics({"historical_mean":[{"security_id":"conid:123"}],
                                    "model":[{"security_id":"conid:123"}]})


    def test_authorized_rating_history_receipt_cutoff_and_dedup(self):
        from atlaspm.research.credit_intelligence import archive_observations,rating_history,_FLAGS
        from unittest.mock import patch
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cwd=Path.cwd()
        os.chdir(temp.name)
        self.addCleanup(os.chdir,cwd)
        Path("local_data").mkdir()
        now=datetime.now(UTC)
        row={"agency":"sp_ratings","identifier_type":"LEI","identifier":"A"*20,
            "scope":"issuer","rating":"BBB","rating_type":"Long Term","effective_raw":"2020-01-01",
            "action_raw":"Downgrade","watch_outlook_raw":None,"basis_raw":[None,None],
            "agency_entity_id":1,"agency_issue_id":None,"published_at":None,"first_available_at":None}
        report={**_FLAGS,"provider":"xpressapi","ratings":[row],"transport":{
                "provider":"xpressapi","sha256":"b"*64,"retrieved_at_utc":now.isoformat()}}
        with patch.dict(os.environ,{"ATLASPM_XPRESSAPI_RIGHTS":"fixture",
                        "ATLASPM_XPRESSAPI_RIGHTS_RETENTION":"1"}):
            self.assertEqual(archive_observations(report)["added"],1)
            self.assertEqual(archive_observations(report)["added"],0)
            old=rating_history(identifier_type="LEI",identifier="A"*20,as_of=(now-timedelta(seconds=1)).isoformat())
            self.assertEqual(old["ratings"],[])
            current=rating_history(identifier_type="LEI",identifier="A"*20,as_of=datetime.now(UTC).isoformat())
            self.assertEqual(len(current["ratings"]),1)
            self.assertIsNone(current["ratings"][0]["rating"]["first_available_at"])
            self.assertFalse(current["approved_for_model_training"])

    def test_queue_supersedes_visible_old_revision(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cwd=Path.cwd()
        os.chdir(temp.name)
        self.addCleanup(os.chdir,cwd)
        Path("local_data").mkdir()
        now=datetime.now(UTC)
        row={"provider":"sec","source_id":"same","kind":"sec_disclosure","subjects":["CIK:0000320193"],
             "published_at":(now-timedelta(minutes=5)).isoformat(),"first_available_at":None,
             "effective_at":None,"evidence_sha256":"a"*64,"delivery":"poll","expiry_seconds":3600}
        first=normalize(row,received_at=(now-timedelta(minutes=2)).isoformat())
        second=normalize({**row,"evidence_sha256":"b"*64},received_at=now.isoformat())
        event_queue.enqueue([first,second])
        result=event_queue.drain(as_of=now.isoformat())
        self.assertEqual(result["completed"],1)
        self.assertEqual(result["superseded"],1)
