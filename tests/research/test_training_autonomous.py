import unittest
from contextlib import contextmanager
from datetime import date,timedelta
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import json

from atlaspm.research.training_autonomous import research,_reserve_holdout
from atlaspm.research.training_strategy_metrics import evaluate,paired_lower
from atlaspm.research.training_decision_policy import paper_decision
from atlaspm.research.training_admission import audit_factor_months
from atlaspm.research.training_engine import train


def fixture(days=110):
    sessions=[]
    for i in range(days):
        d=(date(2020,1,1)+timedelta(days=i)).isoformat()
        t=d+"T16:00:00-05:00"
        assets=[dict(security_id="SYN:"+s,source="synthetic",observed_at=t,
                     available_at=t,close=100*(1+.001*j)**i,
                     features={"momentum":-j,"predictive":j})
                for j,s in enumerate(("AAA","BBB"),1)]
        sessions.append(dict(date=d,close_at=t,assets=assets))
    return dict(mode="synthetic",synthetic_fixture=True,
                feature_names=["momentum","predictive"],sessions=sessions)


@contextmanager
def local_workspace():
    previous=Path.cwd()
    with TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            yield Path("local_data/research.sqlite3")
        finally:
            os.chdir(previous)


def run(p,path):
    return research(p,registry_path=path,holdout_start=62,
                    momentum_feature="momentum",budget=4,
                    minimum_train_sessions=10,bootstrap_draws=1000)


class StrategyTests(unittest.TestCase):
    def test_lag_ties_and_cost(self):
        p=fixture(14)
        rows=[dict(date=s["date"],security_id=a["security_id"],
                   label_end_date=p["sessions"][i+1]["date"],
                   actual=.001*(j+1),prediction=0.)
              for i,s in enumerate(p["sessions"][:-1]) for j,a in enumerate(s["assets"])]
        a=evaluate(rows,p["sessions"],cost_bps=10.)
        b=evaluate(rows,p["sessions"],cost_bps=0.)
        self.assertEqual(a["daily"][0]["entry_date"],p["sessions"][1]["date"])
        self.assertEqual(a["daily"][0]["exit_date"],p["sessions"][2]["date"])
        self.assertEqual(a["daily"][0]["selected"],["SYN:AAA","SYN:BBB"])
        self.assertLess(a["metrics"]["net_return"],b["metrics"]["net_return"])
        self.assertEqual(paired_lower(a,b,seed=8,draws=100,block=2),
                         paired_lower(a,b,seed=8,draws=100,block=2))
        p["sessions"][2]["assets"].pop()
        with self.assertRaisesRegex(ValueError,"MISSING_EXECUTABLE_PATH"):
            evaluate(rows,p["sessions"])

    def test_holdout_intervals(self):
        with local_workspace() as path:
            _reserve_holdout(path,"2020-01-01","2020-03-01","2020-04-01","one")
            with self.assertRaisesRegex(ValueError,"HOLDOUT_ALREADY"):
                _reserve_holdout(path,"2020-01-01","2020-04-02","2020-05-01","two")
            with self.assertRaisesRegex(ValueError,"HOLDOUT_ALREADY"):
                _reserve_holdout(path,"2019-01-01","2019-12-01","2020-02-01","three")

    def test_admission_missing_partition_stays_blocked(self):
        with local_workspace():
            a=audit_factor_months(["2000-02"])
        self.assertEqual(a["status"],"BLOCK_REAL_TRAINING")
        self.assertIn("MISSING_COMPLETED_PARTITION_OR_MANIFEST",a["blockers"])
        self.assertFalse(a["approved_for_model_training"])


@unittest.skipUnless(find_spec("numpy"),"portfolio extra required")
class AutonomousTests(unittest.TestCase):
    def test_autonomous_selection_holdout_and_registry(self):
        p=fixture()
        with local_workspace() as path:
            a=run(p,path)
            self.assertEqual(a["status"],"CHALLENGER_HOLDOUT_PASS_RESEARCH_ONLY")
            self.assertEqual(a["selected_research_strategy"]["model"],"ridge")
            self.assertTrue(any("DUPLICATE_ALLOCATION" in reason
                            for t in a["validation"]["trials"] for reason in t["reasons"]))
            self.assertFalse(a["live_execution_enabled"])
            self.assertFalse(a["model_registry_admission"])
            with self.assertRaisesRegex(ValueError,"HOLDOUT_ALREADY"):
                run(p,path)

    def test_holdout_mutation_does_not_change_selection(self):
        p=fixture()
        with local_workspace() as path:
            a=run(p,path)
        for s in p["sessions"][62:]:
            for asset in s["assets"]:
                asset["close"]*=1.5
        with local_workspace() as path:
            b=run(p,path)
        self.assertEqual(a["validation"],b["validation"])

    def test_real_training_blocked(self):
        p=fixture()
        p["mode"]="historical"
        with local_workspace() as path:
            with self.assertRaisesRegex(ValueError,"REAL_TRAINING_DATA_NOT_APPROVED"):
                run(p,path)

    def test_adaptive_handoff_and_future_outcome_invariance(self):
        example=Path(__file__).resolve().parents[2]/"examples/unified_portfolio_synthetic.json"
        payload=json.loads(example.read_text(encoding="utf-8"))
        p=fixture()
        with local_workspace() as path:
            result=run(p,path)
            extended=fixture(145)
            training=train(extended,horizon=1,minimum_train_sessions=10,
                           models=["ridge","historical_mean"])
            streams=training["predictions"]
            day=streams["ridge"][-1]["date"]
            now=day+"T21:00:00Z"
            payload.update(as_of=now,market_data_available_at=now,history_available_at=now)
            for signal in payload["signals"]:
                signal.update(generated_at=now,available_at=now,data_available_at=now,expires_at=now)
            with patch("atlaspm.research.training_decision_policy.run_unified_portfolio",
                       return_value={"decision":"NO_REBALANCE_NEEDED"}) as engine:
                a=paper_decision(payload,streams,registry_path=path,run_id=result["run_id"])
                self.assertEqual(a["decision"],"NO_REBALANCE_NEEDED")
                first=engine.call_args.args[0]
                for rows in streams.values():
                    for r in rows:
                        if r["label_end_date"]>=day:
                            r["actual"]=999.
                b=paper_decision(payload,streams,registry_path=path,run_id=result["run_id"])
                self.assertEqual(a,b)
                self.assertEqual(first,engine.call_args.args[0])
                self.assertFalse(a["broker_order_submission_enabled"])
            real=paper_decision(payload,streams,registry_path=path,run_id=result["run_id"])
            self.assertIn(real["decision"],("NO_TRADE","NO_REBALANCE_NEEDED",
                                          "SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE"))
            payload["market_data_available_at"]="2030-01-01T00:00:00Z"
            denied=paper_decision(payload,streams,registry_path=path,run_id=result["run_id"])
            self.assertEqual(denied["reason"],"MARKET_OR_HISTORY_LOOKAHEAD")


@unittest.skipUnless(find_spec("scipy"),"portfolio extra required")
class PaperFeedbackTests(unittest.TestCase):
    def test_actual_oms_marks_mature_once(self):
        from atlaspm.engine.unified import run_unified_portfolio
        from atlaspm.engine.contracts import timestamp
        from atlaspm.research.training_registry import register,FLAGS
        from atlaspm.research.training_paper_feedback import record_paper_feedback,matured_feedback
        example=Path(__file__).resolve().parents[2]/"examples/unified_portfolio_synthetic.json"
        payload=json.loads(example.read_text(encoding="utf-8"))
        portfolio=run_unified_portfolio(payload)
        self.assertEqual(portfolio["decision"],"SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE")
        config=dict(as_of=payload["as_of"],maturity_date="2026-11-23",
                    research_run_id="synthetic-study",account_id=payload["account"]["account_id"],
                    annual_margin_rate=.065)
        report=dict(dataset_sha256="a"*64,status="SYNTHETIC_PAPER_DECISION",
                    decision=portfolio["decision"],portfolio=portfolio,**FLAGS)
        with local_workspace() as path:
            decision=register(path,config,report,kind="experiment")
            marks=dict(mode="synthetic",synthetic_fixture=True,
                       observed_at="2026-11-23T21:00:00Z",available_at="2026-11-23T21:00:00Z",
                       marks={"AAA":101.,"BBB":101.})
            with self.assertRaisesRegex(ValueError,"FEEDBACK_NOT_MATURED"):
                record_paper_feedback(registry_path=path,decision_id=decision,observation=marks,
                                      as_of=payload["as_of"])
            result=record_paper_feedback(registry_path=path,decision_id=decision,observation=marks,
                                         as_of=marks["available_at"])
            self.assertAlmostEqual(result["ending_equity"],30238.)
            self.assertEqual(matured_feedback(path,"synthetic-study",config["account_id"],
                timestamp(marks["available_at"],"now")),[])
            self.assertEqual(matured_feedback(path,"synthetic-study",config["account_id"],
                timestamp("2026-11-24T21:00:00Z","now")),[result])
            marks["marks"]["AAA"]=102.
            with self.assertRaisesRegex(ValueError,"PAPER_FEEDBACK_ALREADY_FINALIZED"):
                record_paper_feedback(registry_path=path,decision_id=decision,observation=marks,
                                      as_of=marks["available_at"])
