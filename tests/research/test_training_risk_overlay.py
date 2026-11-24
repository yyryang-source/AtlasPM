import unittest
from copy import deepcopy

from atlaspm.engine.replay import replay
from atlaspm.execution.oms_controls import verify_audit_chain
from atlaspm.risk.leverage_envelope import envelope
from .test_training_strategy_closed_loop import fixture as panel_fixture


def fixture():
    _,market=panel_fixture()
    rows=[]
    for i,row in enumerate(market['sessions'][:4]):
        day=['2020-01-02','2020-01-03','2020-01-06','2020-01-07'][i]
        t=day+'T16:00:00-05:00'
        signals=[dict(symbol=s,security_id='SYN:'+s,sector='test',model_version='fixture',identity_source='synthetic',data_source='synthetic',data_quality='synthetic_test_only',research_eligible=True,model_approved_for_training=False,generated_at=t,available_at=t,data_available_at=t,expires_at=t,factor_score=3-j,model_rank=j+1) for j,s in enumerate(row['close'])]
        rows.append({**row,'date':day,'open_at':day+'T09:30:00-05:00','close_at':t,'signals':signals})
    return dict(mode='synthetic',synthetic_fixture=True,initial_cash=1000,fee_bps=0,slippage_bps=0,cash_reserve=0,top_k=1,rebalance_sessions=1,settlement_sessions=1,sessions=rows)


def scenario(**overrides):
    return {"shock":.2,"maintenance_fraction":.25,"initial_fraction":.5,
            "apr":.06,"days":30,"min_excess_fraction":.1,"max_equity_loss":.5,**overrides}


class EnvelopeTests(unittest.TestCase):
    def test_binding_limits_and_interest(self):
        r=envelope(200000,scenarios=[scenario()])
        self.assertEqual(r["worst_case_gross_ceiling"],2.)
        s=r["scenarios"][0]
        self.assertAlmostEqual(s["assumed_debt"],200000)
        self.assertAlmostEqual(s["assumed_horizon_interest"],200000*.06*30/365)
        self.assertGreaterEqual(s["stressed_excess"],.1*s["stressed_equity"]-1e-7)
        self.assertLessEqual(s["equity_loss_fraction"],.5+1e-7)
        self.assertFalse(r["broker_margin_verified"])
        self.assertFalse(r["live_execution_enabled"])

    def test_fully_invested_failure_not_clamped(self):
        r=envelope(1000,scenarios=[scenario(),scenario(shock=.8)])
        self.assertIsNone(r["worst_case_gross_ceiling"])
        self.assertEqual(r["scenarios"][1]["status"],"FULLY_INVESTED_CASE_FAILS")

    def test_invalid_inputs(self):
        for s in (scenario(apr=-.1),scenario(initial_fraction=.1),scenario(days=True),scenario(shock=1)):
            with self.assertRaises(ValueError):
                envelope(1000,scenarios=[s])


class RiskOverlayTests(unittest.TestCase):
    def test_latch_next_open_reduce_and_no_reentry(self):
        p=fixture()
        p["risk_policy"]={"max_stress_loss_fraction":.05,"equity_shock":.2}
        r=replay(p)
        self.assertTrue(r["risk_halt_latched"])
        self.assertEqual([(t["date"],t["side"]) for t in r["trades"]],
                         [("2020-01-03","BUY"),("2020-01-06","SELL")])
        self.assertEqual(r["remaining_positions_after_halt"],{})
        self.assertTrue(verify_audit_chain(r["risk_audit"],r["risk_audit_root"]))
        changed=deepcopy(r["risk_audit"])
        changed[1]["halt_latched"]=False
        self.assertFalse(verify_audit_chain(changed,r["risk_audit_root"]))
        self.assertFalse(r["live_execution_enabled"])

    def test_prefix_risk_no_future_input(self):
        p=fixture()
        p["risk_policy"]={"max_stress_loss_fraction":.05}
        a=replay(p)
        b=replay({**p,"sessions":p["sessions"][:2]})
        self.assertEqual(a["curve"][:2],b["curve"])
        self.assertEqual(a["risk_audit"][:2],b["risk_audit"])

    def test_invalid_or_absent_policy(self):
        p=fixture()
        self.assertFalse(replay(p)["risk_halt_latched"])
        for policy in ({"max_gross_leverage":2},{"max_drawdown":0},{"unknown":1}):
            with self.assertRaises(ValueError):
                replay({**p,"risk_policy":policy})
