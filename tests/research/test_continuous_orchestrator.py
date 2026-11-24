"""Continuous offline lifecycle and single-queue controls."""
from datetime import UTC,datetime,timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from atlaspm.framework import orchestrator
from atlaspm.framework.continuous_simulation import continuous
from atlaspm.research import event_queue
from atlaspm.research.financial_events import normalize
from atlaspm.research.signal_fusion import fuse,matured_correlations
from atlaspm.research.training_registry import digest


def research(symbol,stamp,values):
    return [{"security_id":"SYN:"+symbol,"asset_type":"stock","category":"factor",
             "kind":"quantitative_prediction","source":"synthetic","model_version":name,
             "evidence":[{"sha256":digest([name,symbol,stamp]),"available_at":stamp,
                          "received_at":stamp,"source":"synthetic","rights_status":"synthetic_test_only"}],
             "generated_at":stamp,"expires_at":stamp,"horizon_days":1,
             "expected_return":value,"uncertainty_radius":.01,"risk_factors":["fixture"],
             "invalidation":["fixture expiry"],"synthetic_fixture":True}
            for name,value in zip(("m1","m2"),values)]


def fixture():
    sessions=[]
    for day,op,cl in (("2020-01-02",100,100),("2020-01-03",100,100),
                       ("2020-01-06",100,90),("2020-01-07",90,90),("2020-01-08",90,90)):
        stamp=day+"T21:00:00Z"
        rows=research("AAA",stamp,(.10,.11))+research("BBB",stamp,(.08,.09))
        if day=="2020-01-06":
            rows=[]  # risk exit must not depend on new research
        sessions.append({"date":day,"open_at":day+"T14:30:00Z","close_at":stamp,"source":"synthetic",
                         "open":{"AAA":op,"BBB":100},"close":{"AAA":cl,"BBB":100},
                         "volume":{"AAA":100000,"BBB":100000},"sectors":{"AAA":"tech","BBB":"health"},
                         "research":rows})
    return {"mode":"synthetic","synthetic_fixture":True,"initial_cash":10000,
            "fee_bps":1,"slippage_bps":1,"cash_reserve":.2,"top_k":1,"rebalance_sessions":1,
            "settlement_sessions":1,"risk_policy":{"max_drawdown":.25,"max_gross_leverage":1.,
            "equity_shock":.2,"max_stress_loss_fraction":.25},
            "execution_policy":{"max_participation":.01,"impact_bps_at_full_participation":10,"fill_fraction":1},
            "position_lifecycle":{"stop_loss":.05,"take_profit":.3,"max_holding_sessions":20},
            "sessions":sessions}


class ContinuousTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cwd=Path.cwd()
        os.chdir(temp.name)
        self.addCleanup(os.chdir,cwd)
        Path("local_data").mkdir()

    def simulate(self,p,**kw):
        return continuous(p,weights={"m1":2,"m2":1},**kw)

    def test_multi_session_buy_stop_exit_settle_and_attribution(self):
        result=self.simulate(fixture())
        sim=result["simulation"]
        self.assertTrue(result["flat_and_settled"])
        self.assertEqual([x["side"] for x in sim["trades"]],["BUY","SELL"])
        self.assertEqual(sim["trades"][0]["date"],"2020-01-03")
        self.assertEqual(sim["trades"][1]["date"],"2020-01-07")
        self.assertTrue(sim["risk_halt_latched"])
        self.assertTrue(any(x["kind"]=="POSITION_STOP" for x in sim["position_lifecycle_audit"]))
        self.assertFalse(result["broker_side_effects"])
        self.assertFalse(result["approved_for_backtest"])

    def test_restart_prefix_no_duplicate_and_conflict(self):
        p=fixture()
        checkpoint="local_data/run.continuous.json"
        first={**p,"sessions":p["sessions"][:3]}
        self.assertFalse(self.simulate(first,checkpoint=checkpoint)["checkpoint_reused"])
        final=self.simulate(p,checkpoint=checkpoint)
        repeated=self.simulate(p,checkpoint=checkpoint)
        self.assertTrue(repeated["checkpoint_reused"])
        self.assertEqual(final["result_sha256"],repeated["result_sha256"])
        p["sessions"][0]["open"]["AAA"]=99
        with self.assertRaisesRegex(ValueError,"PREFIX_CONFLICT"):
            self.simulate(p,checkpoint=checkpoint)

    def test_partial_reject_and_missing_research(self):
        p=fixture()
        p["execution_policy"]["fill_fraction"]=.5
        result=self.simulate(p)
        self.assertTrue(any(o["status"]=="CANCELED" for o in result["simulation"]["orders"]))
        p=fixture()
        p["execution_policy"]["fill_fraction"]=0
        result=self.simulate(p)
        self.assertTrue(all(o["status"]=="REJECTED" for o in result["simulation"]["orders"]))
        self.assertEqual(result["simulation"]["trades"],[])
        p=fixture()
        for row in p["sessions"]:
            row["research"]=[]
        self.assertEqual(self.simulate(p)["simulation"]["trades"],[])

    def test_conflict_stale_permission_and_pretrade_risk(self):
        p=fixture()
        for row in p["sessions"]:
            for x in row["research"]:
                if x["model_version"]=="m2":
                    x["expected_return"]=-.1
        self.assertEqual(self.simulate(p)["simulation"]["trades"],[])
        p=fixture()
        p["risk_policy"]["max_stress_loss_fraction"]=.01
        self.assertEqual(self.simulate(p)["simulation"]["trades"],[])
        p["live_execution_enabled"]=True
        with self.assertRaisesRegex(ValueError,"SYNTHETIC"):
            self.simulate(p)

    def test_system_controls_resume_and_same_tick(self):
        now=datetime.now(UTC)-timedelta(minutes=1)
        row={"provider":"sec","source_id":"fixture","kind":"sec_disclosure","subjects":["CIK:0000320193"],
             "published_at":None,"first_available_at":None,"effective_at":None,"evidence_sha256":"a"*64,
             "delivery":"file","expiry_seconds":3600}
        event_queue.enqueue([normalize(row,received_at=now.isoformat())])
        with patch.object(orchestrator,"market_phase",return_value="market"):
            orchestrator.set_control("stop")
            self.assertEqual(orchestrator.tick(as_of=now.isoformat())["review"]["status"],"PAUSED")
            orchestrator.set_control("resume")
            self.assertEqual(orchestrator.tick(as_of=now.isoformat())["review"]["completed"],1)
            self.assertEqual(orchestrator.tick(as_of=now.isoformat())["review"]["status"],"DUPLICATE_TICK")
            orchestrator.set_control("takeover")
            self.assertTrue(orchestrator.status()["control"]["manual_takeover"])
        self.assertFalse(orchestrator.status()["broker_tasks_enabled"])

    def test_writer_conflict_does_not_modify_state(self):
        orchestrator.set_control("stop")
        before=event_queue.PATH.read_bytes()
        lock=event_queue.PATH.with_suffix(".lock")
        lock.touch()
        with self.assertRaises(FileExistsError):
            orchestrator.set_control("resume")
        self.assertEqual(before,event_queue.PATH.read_bytes())
        lock.unlink()

    def test_decay_and_matured_model_correlation(self):
        stamp="2020-01-02T21:00:00Z"
        rows=research("AAA",stamp,(.1,.12))
        rows[0]["evidence"][0].update(available_at="2020-01-02T20:00:00Z",received_at="2020-01-02T20:00:00Z")
        result=fuse(rows,as_of=stamp,weights={"m1":1,"m2":1},half_life_seconds=3600)
        self.assertAlmostEqual(result["groups"][0]["experimental_weights"]["m1"],1/3)
        streams={}
        for name,mult in (("a",1),("b",2)):
            streams[name]=[{"date":f"2020-01-0{i}","security_id":"SYN:AAA",
                            "label_end_date":f"2020-01-0{i+1}","actual":0.,"prediction":i*.01*mult}
                           for i in range(1,5)]
        report=matured_correlations(streams,as_of_date="2020-01-05",minimum_dates=3)
        self.assertEqual(report["pairs"][0]["matured_dates"],3)
        self.assertAlmostEqual(report["pairs"][0]["correlation"],1.)
        self.assertFalse(report["weights_promoted"])


    def test_joint_research_keeps_missing_specialists_explicit(self):
        from atlaspm.research.intelligence import joint_local_review
        stamp="2020-01-02T21:00:00Z"
        out=joint_local_review(security_id="SYN:AAA",asset_type="stock",as_of=stamp,
                               events=[],research_rows=research("AAA",stamp,(.1,.11)),
                               weights={"m1":1,"m2":1})
        self.assertEqual(out["coverage"]["factor"],"available")
        self.assertEqual(out["coverage"]["credit"],"missing")
        self.assertFalse(out["external_ai_allowed"])
        self.assertFalse(out["trade_authorized"])

    def test_old_availability_cannot_be_refreshed_by_recent_receipt(self):
        from atlaspm.research.research_contract import contract
        stamp="2020-01-02T21:00:00Z"
        row=research("AAA",stamp,(.1,.11))[0]
        row["evidence"][0]["available_at"]="2019-12-01T00:00:00Z"
        self.assertFalse(contract(row,as_of=stamp)["numeric_eligible"])


    def test_option_multiplier_and_bond_duration_not_equity_weights(self):
        from atlaspm.engine.autonomous_decision import multiasset_position_review
        stamp="2020-01-02T21:00:00Z"
        option={"type":"option","identifier":"SYN:OPT","mark_age_seconds":0,"mark_source":"synthetic",
                "contracts":2,"multiplier":100,"mark_premium":3,"right":"call","underlying":"SYN:AAA",
                "strike":100,"expiration_date":"2020-03-20"}
        result=multiasset_position_review(option,as_of=stamp,risk_inputs={"observed_at":stamp,
                "underlying_price":100,"delta":.5,"gamma":.02,"vega_per_vol_point":.1})
        self.assertEqual(result["metrics"]["market_value"],600)
        self.assertEqual(result["metrics"]["delta_dollar_exposure"],10000)
        self.assertEqual(result["decision"],"NO_TRADE")
        bond={"type":"treasury","identifier":"SYN:BOND","mark_age_seconds":0,"mark_source":"synthetic",
              "face_value":10000,"clean_price_per_100":98,"accrued_interest_per_100":2,
              "maturity_date":"2030-01-01","coupon_rate":.04,"modified_duration":7}
        result=multiasset_position_review(bond,as_of=stamp,risk_inputs={"observed_at":stamp})
        self.assertAlmostEqual(result["metrics"]["dv01_usd_per_bp"],7)
        self.assertFalse(result["trade_authorized"])
