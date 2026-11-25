"""Regression tests for opt-in hypothetical margin replay."""
import unittest
from copy import deepcopy

from atlaspm.engine.replay import replay
from atlaspm.engine.replay_funding import MarginFunding
from atlaspm.execution.oms_controls import verify_audit_chain


def fixture():
    rows=[]
    for day,price,score in [("2020-01-02",100,2),("2020-01-03",100,2),
                            ("2020-01-06",100,2),("2020-01-07",100,2),
                            ("2020-01-08",100,2)]:
        t=day+"T16:00:00-05:00"
        signal=dict(symbol="A",security_id="SYN:A",sector="test",model_version="fixture",
                    identity_source="synthetic",data_source="synthetic",data_quality="synthetic_test_only",
                    research_eligible=True,model_approved_for_training=False,generated_at=t,
                    available_at=t,data_available_at=t,expires_at=t,factor_score=score,model_rank=1)
        rows.append(dict(date=day,open_at=day+"T09:30:00-05:00",close_at=t,source="synthetic",
                         open={"A":price},close={"A":price},signals=[signal]))
    return dict(mode="synthetic",synthetic_fixture=True,initial_cash=1000,fee_bps=0,
                slippage_bps=0,cash_reserve=0,top_k=1,rebalance_sessions=5,
                settlement_sessions=1,sessions=rows,
                margin_policy=dict(target_gross=1.5,initial_fraction=.5,maintenance_fraction=.3,annual_apr=.1),
                risk_policy=dict(max_gross_leverage=2,max_drawdown=.25,equity_shock=.2,max_stress_loss_fraction=.5))


class MarginReplayTests(unittest.TestCase):
    def test_borrowing_and_weekend_accrual(self):
        out=replay(fixture())
        self.assertEqual(out["execution_model"],"synthetic_next_open_assumed_margin")
        self.assertAlmostEqual(out["curve"][1]["principal"],500)
        self.assertAlmostEqual(out["curve"][2]["financing_interest_today"],500*.1*3/365)
        self.assertAlmostEqual(out["financing_interest_total"],500*.1*5/365)
        self.assertAlmostEqual(out["curve"][-1]["equity"],1000-out["financing_interest_total"])
        self.assertFalse(out["live_execution_enabled"])
        self.assertFalse(out["broker_order_submission_enabled"])

    def test_gap_halt_sale_and_settled_repayment(self):
        p=fixture()
        for row in p["sessions"][2:]:
            row["open"]["A"]=row["close"]["A"]=70
        out=replay(p)
        self.assertTrue(out["risk_halt_latched"])
        self.assertEqual([(t["date"],t["side"]) for t in out["trades"]],
                         [("2020-01-03","BUY"),("2020-01-06","SELL")])
        self.assertGreater(out["curve"][2]["principal"],0)
        self.assertEqual(out["curve"][3]["principal"],0)
        self.assertEqual(out["curve"][3]["accrued_interest"],0)
        self.assertAlmostEqual(out["principal_repaid_total"],out["borrowed_total"])
        self.assertAlmostEqual(out["interest_paid_total"],out["financing_interest_total"])
        self.assertTrue(verify_audit_chain(out["risk_audit"],out["risk_audit_root"]))
        self.assertAlmostEqual(out["curve"][-1]["equity"]-1000,
            sum(x["overnight_pnl"]+x["intraday_pnl"]-x["trade_cost"]-x["financing_interest_today"] for x in out["curve"]))

    def test_cash_default_unchanged(self):
        p=fixture()
        del p["margin_policy"]
        p["risk_policy"]["max_gross_leverage"]=1
        out=replay(p)
        self.assertEqual(out["execution_model"],"synthetic_next_open_cash_only")
        self.assertNotIn("margin_debt",out)
        self.assertEqual(out["curve"][1]["holdings"],{"A":10})

    def test_bad_margin_config_rejected(self):
        for update in ({"cash_reserve":.1},{"optimizer":{}},{"risk_policy":None},
                       {"mode":"historical"},{"margin_policy":{**fixture()["margin_policy"],"target_gross":3}}):
            with self.assertRaises(ValueError):
                replay({**fixture(),**update})

    def test_budget_excludes_unsettled_and_bounds_costs(self):
        m=MarginFunding.from_policy(fixture()["margin_policy"])
        self.assertEqual(m.buy_budget(cash=0,pending=1000,gross=0,equity=1000,adverse_cost_ratio=.01),0)
        limit=m.buy_budget(cash=1000,pending=0,gross=0,equity=1000,adverse_cost_ratio=.01,gross_limit=1.5)
        self.assertLessEqual(limit,1500/1.015+1e-8)
        self.assertGreater(limit,1000)

    def test_existing_debt_cost_headroom_even_when_no_new_loan(self):
        m=MarginFunding(1.5,.5,.3,.1,principal=749)
        # Cash is intentionally present with existing debt to independently
        # test the funding bound before the replay's normal repayment sweep.
        limit=m.buy_budget(cash=1000,pending=0,gross=500,equity=751,adverse_cost_ratio=.01)
        self.assertAlmostEqual(limit,200)
        self.assertLessEqual(m.liability,751-limit*.01+1e-8)

    def test_prefix_invariance(self):
        p=fixture()
        a=replay(p)
        short=deepcopy(p)
        short["sessions"]=short["sessions"][:3]
        b=replay(short)
        self.assertEqual(a["curve"][:3],b["curve"])
        self.assertEqual(a["risk_audit"][:3],b["risk_audit"])
