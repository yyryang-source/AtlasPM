"""Continuous offline lifecycle and single-queue controls."""
from datetime import UTC,datetime,timedelta
import importlib.util
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


from atlaspm.framework.continuous_simulation import fixture_research as research,synthetic_fixture as fixture


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

    def test_specialist_status_separates_metadata_and_blocked_local_calls(self):
        import io,json
        from contextlib import redirect_stdout
        now="2020-01-10T21:00:00Z"
        def event(kind):
            return normalize({"provider":"synthetic","source_id":"SYN:"+kind,"kind":kind,
                "subjects":["SYN:AAA"],"published_at":now,"first_available_at":now,
                "effective_at":None,"evidence_sha256":"a"*64,"delivery":"file","expiry_seconds":3600},
                received_at=now)
        # Technical request has no data at this cutoff; metadata still completes.
        task={"route":"technical_local","params":{"start":"2019-01-10","end":"2020-01-10","symbols":["AAA"]}}
        event_queue.enqueue([event("volume_anomaly"),event("news")])
        result=event_queue.drain(as_of=now,local_bindings=[
            {"kind":"volume_anomaly","subject":"SYN:AAA","task":task}])
        self.assertEqual(result["completed"],2)
        status=orchestrator.status()
        self.assertEqual(status["research_topic_counts_scope"],"event_metadata_only")
        self.assertEqual(status["research_topic_counts"]["technical"],1)
        self.assertEqual(status["blocked_local_specialist_tasks"],1)
        self.assertEqual(status["blocked_research_tasks"],1)
        self.assertEqual(status["completed_events_without_local_receipts"],1)
        self.assertEqual(status["local_specialist_completed_by_route"],{})
        self.assertFalse(status["professional_research_coverage_verified"])
        self.assertFalse(status["broker_tasks_enabled"])
        path=Path("local_data/task.json")
        path.write_text(json.dumps({"as_of":now,"tasks":[task]}),encoding="utf-8")
        with patch("sys.argv",["orchestrator","research","--file",str(path)]), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(orchestrator.main(),1)
        report=json.loads(output.getvalue())
        self.assertEqual(report["status"],"LOCAL_RESEARCH_BLOCKED")
        self.assertEqual(report["blocked_tasks"],1)
        self.assertFalse(report["trade_authorized"])
        # A completed descriptive event review must not claim verified coverage.
        path.write_text(json.dumps({"as_of":now,"tasks":[{"route":"events","params":{"events":[event("news")]}}]}),
                        encoding="utf-8")
        with patch("sys.argv",["orchestrator","research","--file",str(path)]), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(orchestrator.main(),0)
        self.assertEqual(json.loads(output.getvalue())["status"],"LOCAL_RESEARCH_REVIEWED")

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


    @unittest.skipUnless(importlib.util.find_spec("scipy") and
                         importlib.util.find_spec("pandas_market_calendars"),
                         "portfolio/calendar extras required")
    def test_offline_entry_real_modules_and_isolated_fixture(self):
        out=orchestrator.offline_release(checkpoint="local_data/rc.continuous.json")
        self.assertEqual(out["status"],"OFFLINE_FIXTURE_PASS",out["checks"])
        self.assertTrue(all(out["checks"].values()))
        self.assertFalse(out["release_candidate_approved"])
        self.assertFalse(out["real_api_connected"])
        self.assertFalse(event_queue.PATH.exists())
        orchestrator.set_control("takeover")
        self.assertEqual(orchestrator.offline_release()["status"],"PAUSED")

    def test_synthetic_event_cannot_claim_real_subject(self):
        row=fixture()["sessions"][0]["events"][0]
        raw={k:v for k,v in row.items() if k not in {"received_at","event_id","revision","rights_status"}}
        raw["subjects"]=["conid:123"]
        with self.assertRaisesRegex(ValueError,"synthetic subject"):
            normalize(raw,received_at=row["received_at"])


    def test_event_task_routing_persists_research_and_rejects_real_identity_guess(self):
        now=datetime.now(UTC)-timedelta(minutes=1)
        rows=[]
        for kind in ("news","options_flow","credit_rating","analyst_rating","sec_disclosure",
                     "rate_decision","index_change","volume_anomaly","bond_trade"):
            rows.append(normalize({"provider":"synthetic","source_id":"SYN:"+kind,"kind":kind,
                "subjects":["SYN:AAA"],"published_at":now.isoformat(),"first_available_at":now.isoformat(),
                "effective_at":None,"evidence_sha256":"a"*64,"delivery":"file","expiry_seconds":3600},
                received_at=now.isoformat()))
        event_queue.enqueue(rows)
        self.assertEqual(event_queue.drain(as_of=now.isoformat(),limit=20)["completed"],len(rows))
        state=event_queue._read_state(event_queue.PATH)
        tasks=[task for item in state.values() for task in item["report"]["specialist_tasks"]]
        self.assertTrue({"news","options","credit","analyst","sec","macro","industry","technical","bond","factor"}
                        <= {t["topic"] for t in tasks})
        self.assertTrue(all(not t["numeric_eligible"] for t in tasks))
        event_queue.enqueue(rows)
        self.assertEqual(event_queue.drain(as_of=now.isoformat())["completed"],0)
        real=normalize({"provider":"sec","source_id":"real-shaped-fixture","kind":"sec_disclosure",
            "subjects":["CIK:0000320193"],"published_at":None,"first_available_at":None,"effective_at":None,
            "evidence_sha256":"b"*64,"delivery":"file","expiry_seconds":3600},received_at=now.isoformat())
        event_queue.enqueue([real])
        self.assertEqual(event_queue.drain(as_of=now.isoformat())["completed"],1)
        state=event_queue._read_state(event_queue.PATH)
        blocked=[task for item in state.values() for task in item["report"]["blocked_specialist_tasks"]]
        self.assertTrue(all(t["reason"]=="VERIFIED_INSTRUMENT_MAPPING_REQUIRED" for t in blocked))
        self.assertEqual(len(blocked),2)

    def test_atomic_write_failure_retries_without_committing_partial_tasks(self):
        row=fixture()["sessions"][0]["events"][0]
        event_queue.enqueue([row])
        before=event_queue.PATH.read_bytes()
        with patch.object(event_queue,"_atomic_write",side_effect=OSError("injected write failure")):
            with self.assertRaises(OSError):
                event_queue.drain(as_of=row["received_at"])
        self.assertEqual(event_queue.PATH.read_bytes(),before)
        self.assertFalse(event_queue.PATH.with_suffix(".lock").exists())
        self.assertEqual(event_queue.drain(as_of=row["received_at"])["completed"],1)
        self.assertEqual(event_queue.drain(as_of=row["received_at"])["completed"],0)


    def test_multiasset_oms_persisted_partial_fill_resume_and_rollback_refusal(self):
        from atlaspm.framework.autonomous_cycle import multiasset_fixture
        checkpoint="local_data/mixed.oms-fixture.json"
        partial=multiasset_fixture(checkpoint=checkpoint,event_count=3)
        self.assertEqual(partial["report"]["orders"][0]["filled_quantity"],4)
        saved=event_queue._read_state(checkpoint)["oms_fixture"]
        self.assertEqual(saved["orders"][0]["filled_quantity"],4)
        final=multiasset_fixture(checkpoint=checkpoint)
        self.assertEqual(final["report"]["decision"],"PAPER_REPLAY_COMPLETED")
        self.assertTrue(all(o["status"]=="FILLED" for o in final["report"]["orders"]))
        self.assertEqual(final["result_sha256"],multiasset_fixture(checkpoint=checkpoint)["result_sha256"])
        with self.assertRaisesRegex(ValueError,"PREFIX_CONFLICT"):
            multiasset_fixture(checkpoint=checkpoint,event_count=3)
        self.assertFalse(final["broker_side_effects"])


    def test_local_specialist_dispatch_dedup_and_blocked_wire_access(self):
        from atlaspm.research.intelligence import local_specialist_tasks
        session=fixture()["sessions"][0]
        task={"route":"events","params":{"events":session["events"]}}
        result=local_specialist_tasks([task,task,{"route":"ibkr","params":{}},
            {"route":"unusual_whales","params":{}}],as_of=session["close_at"])
        self.assertEqual(len(result["tasks"]),3)
        self.assertEqual(result["tasks"][0]["status"],"LOCAL_REVIEW_COMPLETED")
        self.assertGreater(len(result["tasks"][0]["report"]["research_tasks"]),0)
        self.assertTrue(all(x["status"].startswith("BLOCKED") for x in result["tasks"][1:]))
        self.assertEqual(result["network_calls"],0)
        self.assertFalse(result["trade_authorized"])


    def test_cancel_pending_and_unknown_recovery_preserve_partial_fill(self):
        from atlaspm.framework.autonomous_cycle import multiasset_fixture
        for scenario,expected in (("cancel","CANCELED"),("unknown","UNKNOWN")):
            path="local_data/"+scenario+".oms-fixture.json"
            pending=multiasset_fixture(checkpoint=path,event_count=4,scenario=scenario)
            self.assertEqual(pending["report"]["orders"][0]["status"],"CANCEL_REQUESTED")
            final=multiasset_fixture(checkpoint=path,scenario=scenario)
            self.assertEqual(final["report"]["orders"][0]["status"],expected)
            self.assertEqual(final["report"]["orders"][0]["filled_quantity"],4)
            self.assertAlmostEqual(final["report"]["account_replay"]["net_liquidation_reconciliation_gap"],0)
            if scenario=="unknown":
                self.assertEqual(final["report"]["decision"],"HALT_RECONCILIATION")
            with self.assertRaisesRegex(ValueError,"PREFIX_CONFLICT"):
                multiasset_fixture(checkpoint=path,scenario="normal")


    def test_mixed_asset_candidate_ranking_reuses_preflight_and_blocks_missing_forecasts(self):
        import json
        from atlaspm.engine.autonomous_decision import rank_synthetic_baskets
        root=Path(__file__).resolve().parents[2]/"examples"
        ledger=json.loads((root/"institutional_batch_ledger.json").read_text())
        plan=json.loads((root/"institutional_batch_plan.json").read_text())
        stamp=ledger["valuation_at"]
        rows=[]
        for symbol,kind in (("QQQ","etf"),("91282CDE0","bond"),("QQQ-2027-01-15-P-280","option")):
            rows.extend({**r,"asset_type":kind} for r in research(symbol,stamp,(.10,.11)))
        out=rank_synthetic_baskets(ledger,[plan],rows=rows,as_of=stamp,weights={"m1":2,"m2":1},
            horizon_days=1,annual_financing_rate=.06,risk_penalty=0)
        self.assertEqual(out["research_candidate"],plan["batch_id"])
        self.assertFalse(out["trade_authorized"])
        out=rank_synthetic_baskets(ledger,[plan],rows=rows[:-2],as_of=stamp,weights={"m1":2,"m2":1},
            horizon_days=1,annual_financing_rate=.06,risk_penalty=0)
        self.assertEqual(out["decision"],"NO_TRADE")
        from copy import deepcopy
        other=deepcopy(plan)
        other["batch_id"]="OTHER"
        other["policy"]["max_gross_leverage"]=2.0
        with self.assertRaisesRegex(ValueError,"BASKET_RISK_POLICY_MISMATCH"):
            rank_synthetic_baskets(ledger,[plan,other],rows=rows,as_of=stamp,weights={"m1":2,"m2":1},
                horizon_days=1,annual_financing_rate=.06,risk_penalty=0)

    @unittest.skipUnless(importlib.util.find_spec("scipy"),"portfolio extra required")
    def test_joint_optimizer_constructs_discrete_legs_and_never_fabricates_preview(self):
        import json
        from atlaspm.engine.allocation import optimize_multiasset
        root=Path(__file__).resolve().parents[2]/"examples"
        ledger=json.loads((root/"institutional_batch_ledger.json").read_text())
        context=json.loads((root/"institutional_batch_plan.json").read_text())
        stamp=ledger["valuation_at"]
        put={"type":"option","identifier":"QQQ-2027-01-15-P-280","underlying":"QQQ",
             "expiration_date":"2027-01-15","right":"put","strike":280,"contracts":1,
             "multiplier":100,"mark_premium":5,"mark_source":"synthetic","mark_age_seconds":15}
        symbols=["QQQ","91282CDE0",put["identifier"]]
        inputs={i:{"observed_at":stamp,"cost_bps":0,"max_trade_notional":10000} for i in symbols}
        inputs[symbols[0]]["expected_return"]=-.2
        inputs[symbols[1]].update(expected_return=.06,credit_class="US_TREASURY",face_increment=100)
        inputs[symbols[2]].update(expected_return=.3,delta=-.2,gamma=.002,vega_per_vol_point=.1,
                                  trading_permission=True)
        market={"as_of":stamp,"horizon_days":10,"symbols":symbols,"inputs":inputs,"new_puts":[put],
                "covariance":[[.01,0,0],[0,.001,0],[0,0,.04]],"cash_return":0,"annual_financing_rate":.04}
        limits={"min_cash_fraction":.01,"risk_aversion":1,"max_turnover":.3,"max_volatility":.2,
                "max_dv01_per_nlv":.001,"max_abs_delta_per_nlv":1,"max_gamma_per_nlv":1,
                "max_vega_per_nlv":1,"max_option_weight":.02}
        out=optimize_multiasset(ledger,context,market,limits)
        self.assertEqual(out["status"],"BLOCKED_PENDING_INDEPENDENT_PREFLIGHT",out)
        actions={leg["action"] for leg in out["legs"]}
        self.assertTrue({"SELL_EQUITY","BUY_TREASURY","BUY_PUT"}<=actions,actions)
        self.assertLess(out["discrete_weights"]["QQQ"],200*300/123950)
        self.assertGreaterEqual(out["settled_cash_after"],.01*123950+300-1e-5)
        self.assertNotIn("basket_preview",out["draft_plan"])
        self.assertEqual(out["independent_preflight"]["decision"],"BLOCK")
        self.assertTrue(out["independent_preflight"]["pro_forma_unexecuted"]["self_financing_identity_verified"])
        self.assertFalse(out["trade_authorized"])
        self.assertFalse(out["global_optimum_verified"])
        with patch("scipy.optimize.minimize",side_effect=ValueError("solver failure")):
            refused=optimize_multiasset(ledger,context,market,limits)
        self.assertEqual(refused["status"],"BLOCKED_CONTINUOUS_SOLVE")
        self.assertEqual(refused["legs"],[])
        self.assertFalse(refused["trade_authorized"])
        inputs[symbols[2]]["trading_permission"]=False
        with self.assertRaisesRegex(ValueError,"OPTION_PERMISSION_REQUIRED"):
            optimize_multiasset(ledger,context,market,limits)
        inputs[symbols[2]]["trading_permission"]=True
        market["symbols"]=symbols[::-1]
        with self.assertRaisesRegex(ValueError,"IDENTITY_OR_COVARIANCE_ORDER"):
            optimize_multiasset(ledger,context,market,limits)

    def test_process_death_after_commit_leaves_recoverable_queue(self):
        import subprocess,sys
        row=fixture()["sessions"][0]["events"][0]
        event_queue.enqueue([row])
        code=(
            "import os; from atlaspm.research import event_queue as q; "
            "write=q._atomic_write; "
            "q._atomic_write=lambda p,f:(write(p,f),os._exit(23)); "
            "q.drain(as_of="+repr(row["received_at"])+")")
        env=dict(os.environ)
        env["PYTHONPATH"]=str(Path(__file__).resolve().parents[2]/"src")+os.pathsep+env.get("PYTHONPATH","")
        child=subprocess.run([sys.executable,"-c",code],env=env,timeout=30,capture_output=True)
        self.assertEqual(child.returncode,23,child.stderr.decode(errors="replace"))
        lock=event_queue.PATH.with_suffix(".lock")
        self.assertTrue(lock.exists())
        with self.assertRaises(FileExistsError):
            event_queue.drain(as_of=row["received_at"])
        # We have joined the only writer process. Explicit operator cleanup,
        # never automatic time-based lock stealing.
        lock.unlink()
        self.assertEqual(event_queue.drain(as_of=row["received_at"])["completed"],0)
        self.assertEqual(next(iter(event_queue._read_state(event_queue.PATH).values()))["state"],"completed")


    def test_bound_event_calls_existing_technical_reader_and_deduplicates(self):
        from contextlib import closing
        from atlaspm.data import market_db
        from atlaspm.research.intelligence import local_specialist_tasks
        from datetime import date
        now="2020-01-10T21:00:00Z"
        start=date(2019,1,10)
        with closing(market_db.connect(market_db.DB_PATH)) as con, con:
            con.executemany("INSERT INTO equity_daily VALUES (?,?,?,?)",
                [("AAA",(start+timedelta(days=j)).isoformat(),100+j*.1,"fixture")
                 for j in range(365) if (start+timedelta(days=j)).weekday()<5])
        task={"route":"technical_local","params":{"start":"2019-01-10","end":"2020-01-09","symbols":["AAA"]}}
        out=local_specialist_tasks([task],as_of=now)["tasks"][0]
        self.assertEqual(out["status"],"LOCAL_REVIEW_COMPLETED")
        self.assertGreaterEqual(out["report"]["rows_read"],253)
        self.assertFalse(out["report"]["numeric_eligible"])
        row=normalize({"provider":"synthetic","source_id":"SYN:technical","kind":"volume_anomaly",
            "subjects":["SYN:AAA"],"published_at":now,"first_available_at":now,"effective_at":None,
            "evidence_sha256":"a"*64,"delivery":"file","expiry_seconds":3600},received_at=now)
        event_queue.enqueue([row])
        bindings=[{"kind":"volume_anomaly","subject":"SYN:AAA","task":task}]
        result=event_queue.drain(as_of=now,local_bindings=bindings)
        self.assertEqual(result["local_research_calls"],1)
        saved=next(iter(event_queue._read_state(event_queue.PATH).values()))["report"]["local_specialist_receipts"]
        self.assertEqual(saved[0]["report_sha256"],out["report_sha256"])
        self.assertNotIn("report",saved[0])
        self.assertEqual(event_queue.drain(as_of=now,local_bindings=bindings)["local_research_calls"],0)
        task["params"]["end"]="2020-01-10"
        self.assertTrue(local_specialist_tasks([task],as_of=now)["tasks"][0]["status"].startswith("BLOCKED"))


    def test_settlement_fixture_reuses_option_and_treasury_cashflow_engines(self):
        from atlaspm.framework.autonomous_cycle import settlement_fixture
        out=settlement_fixture()
        self.assertTrue(all(out["checks"].values()),out["checks"])
        self.assertAlmostEqual(out["option_final_cash"],11838)
        self.assertAlmostEqual(out["bond_final_cash"],10500)
        self.assertFalse(out["short_assignment_supported"])
        self.assertFalse(out["broker_side_effects"])

    def test_fsync_failure_preserves_previous_committed_queue(self):
        row=fixture()["sessions"][0]["events"][0]
        event_queue.enqueue([row])
        before=event_queue.PATH.read_bytes()
        with patch.object(event_queue.os,"fsync",side_effect=OSError("sync failure")):
            with self.assertRaises(OSError):
                event_queue.drain(as_of=row["received_at"])
        self.assertEqual(before,event_queue.PATH.read_bytes())
        self.assertFalse(event_queue.PATH.with_suffix(".lock").exists())
        self.assertEqual(event_queue.drain(as_of=row["received_at"])["completed"],1)


    def test_local_artifact_cutoff_blocks_before_loading(self):
        from atlaspm.research.intelligence import local_specialist_tasks
        task={"route":"local_artifact","params":{"artifact_db":"local_data/a.sqlite3","artifact_id":"a"*64,
              "payload":{"sessions":[{"close_at":"2020-01-03T21:00:00Z"}]}}}
        with patch("atlaspm.research.intelligence.predict_local_artifact") as loader:
            result=local_specialist_tasks([task],as_of="2020-01-02T21:00:00Z")
            loader.assert_not_called()
        self.assertTrue(result["tasks"][0]["status"].startswith("BLOCKED"))
