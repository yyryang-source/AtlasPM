import unittest
from copy import deepcopy
from datetime import date,timedelta
from importlib.util import find_spec
from unittest.mock import patch

from atlaspm.research import strategy_closed_loop as loop
from atlaspm.engine.replay import replay


def fixture():
    sessions=[]
    prices=[]
    sectors={"A":"test","B":"test","C":"test"}
    for i in range(85):
        day=(date(2020,1,2)+timedelta(days=i)).isoformat()
        close=day+"T16:00:00-05:00"
        marks={s:100*(1+.001*j)**i for j,s in enumerate(sectors,1)}
        assets=[dict(security_id="SYN:"+s,source="synthetic",observed_at=close,
                     available_at=close,close=marks[s],features={"momentum":j,"variation":i%3})
                for j,s in enumerate(sectors,1)]
        sessions.append(dict(date=day,close_at=close,assets=assets))
        prices.append(dict(date=day,open_at=day+"T09:30:00-05:00",close_at=close,
                           source="synthetic",open=dict(marks),close=marks))
    return (dict(mode="synthetic",synthetic_fixture=True,feature_names=["momentum","variation"],sessions=sessions),
            dict(mode="synthetic",synthetic_fixture=True,sectors=sectors,sessions=prices))


def options():
    return dict(model_config={"minimum_train_sessions":10,"test_sessions":5},
                model_candidates=[{"model":"ridge","ridge_alpha":.1},{"model":"historical_mean"}],
                model_selection_end=31,strategy_holdout_start=56,
                strategies=[{"top_k":1,"rebalance_sessions":1},{"top_k":2,"rebalance_sessions":5}],
                execution={"initial_cash":10000,"fee_bps":5,"slippage_bps":5})


@unittest.skipUnless(find_spec("numpy"),"numpy extra required")
class ClosedLoopTests(unittest.TestCase):
    def test_end_to_end_cash_and_next_open(self):
        p,m=fixture()
        r=loop.run(p,m,**options())
        self.assertEqual(r["status"],"SYNTHETIC_CASH_CLOSED_LOOP_COMPLETE",r)
        self.assertEqual(r["selection"]["selected_model"],"ridge")
        self.assertTrue(r["holdout"]["trade_count"])
        self.assertFalse(r["approved_for_model_training"])
        self.assertFalse(r["live_execution_enabled"])
        self.assertFalse(r["broker_order_submission_enabled"])
        self.assertTrue(all(t["signal_date"]<t["date"] for t in r["holdout"]["trades"]))
        self.assertTrue(all(x["settled_cash"]>=-1e-8 for x in r["holdout"]["curve"]))
        self.assertEqual(r["holdout"]["curve"][0]["holdings"],{})
        self.assertAlmostEqual(r["holdout"]["curve"][-1]["equity"]-10000,
                               sum(x["overnight_pnl"]+x["intraday_pnl"]-x["trade_cost"] for x in r["holdout"]["curve"]))

    def test_holdout_does_not_choose_model_or_strategy(self):
        p,m=fixture()
        a=loop.run(p,m,**options())
        for session,row in zip(p["sessions"][56:],m["sessions"][56:]):
            for item in session["assets"]:
                item["close"]*=1.5
                s=item["security_id"][4:]
                row["close"][s]*=1.5
                row["open"][s]*=1.5
        b=loop.run(p,m,**options())
        self.assertEqual(a["selection"],b["selection"])
        self.assertEqual(a["strategy_trials"],b["strategy_trials"])
        self.assertEqual(a["selected_strategy"],b["selected_strategy"])

    def test_reject_misaligned_mark_and_real_input_before_training(self):
        p,m=fixture()
        m["sessions"][0]["close"]["A"]+=1
        with patch.object(loop,"tournament") as fit:
            with self.assertRaisesRegex(ValueError,"TRAINING_AND_EXECUTION_CLOSE_MISMATCH"):
                loop.run(p,m,**options())
            with self.assertRaisesRegex(ValueError,"REAL_TRAINING_DATA_NOT_APPROVED"):
                loop.run({**p,"mode":"historical"},m,**options())
            fit.assert_not_called()

    def test_no_holdout_fit_when_all_strategies_fail(self):
        p,m=fixture()
        with patch.object(loop,"_evaluate",return_value={"statistics":{"sharpe_zero_rf":None,
                    "max_drawdown":0.},"trade_count":0}),patch.object(loop,"train",wraps=loop.train) as fit:
            r=loop.run(p,m,**options())
        self.assertEqual(r["status"],"NO_VALID_STRATEGY")
        self.assertFalse(r["holdout_evaluated"])
        self.assertEqual(fit.call_count,1)

    @unittest.skipUnless(find_spec("scipy"),"portfolio extra required")
    def test_existing_optimizer_path(self):
        p,m=fixture()
        config=options()
        config["strategies"]=[
            {"top_k":2,"rebalance_sessions":2,"optimizer":{"method":"equal_weight","lookback_sessions":3}},
            {"top_k":2,"rebalance_sessions":3,"optimizer":{"method":"minimum_variance","lookback_sessions":3}}]
        r=loop.run(p,m,**config)
        self.assertEqual(r["status"],"SYNTHETIC_CASH_CLOSED_LOOP_COMPLETE",r)
        self.assertTrue(r["holdout"]["allocations"])
        self.assertIn("equal_weight_price_benchmark",r["holdout"])
        for allocation in r["holdout"]["allocations"]:
            self.assertLessEqual(sum(allocation["weights"].values()),1+1e-8)
            self.assertLessEqual(max(allocation["weights"].values()),.6+1e-8)
            self.assertLessEqual(allocation["estimation_last_date"],allocation["signal_date"])

    def test_assumed_margin_closed_loop(self):
        p,m=fixture()
        config=options()
        for s in config["strategies"]:
            s["cash_reserve"]=0
        config["execution"].update(
            margin_policy={"target_gross":1.5,"initial_fraction":.5,"maintenance_fraction":.3,"annual_apr":.06},
            risk_policy={"max_gross_leverage":2,"max_drawdown":.25,"max_stress_loss_fraction":.5})
        r=loop.run(p,m,**config)
        self.assertEqual(r["status"],"SYNTHETIC_CASH_CLOSED_LOOP_COMPLETE",r)
        h=r["holdout"]
        self.assertEqual(h["execution_model"],"synthetic_next_open_assumed_margin")
        self.assertGreater(h["assumed_financing"]["borrowed_total"],0)
        self.assertGreater(h["assumed_financing"]["financing_interest_total"],0)
        self.assertFalse(r["live_execution_enabled"])
        self.assertFalse(r["broker_order_submission_enabled"])

    def test_adaptive_signal_uses_existing_ensemble(self):
        p,m=fixture()
        r=loop.run(p,m,**options(),signal_model="adaptive_ensemble",
                   ensemble={"window_dates":3,"minimum_dates":2})
        self.assertEqual(r["status"],"SYNTHETIC_CASH_CLOSED_LOOP_COMPLETE",r)


class ReplayPolicyTests(unittest.TestCase):
    def test_explicit_hold_only_and_default_reject(self):
        _,m=fixture()
        rows=deepcopy(m["sessions"][:2])
        for row in rows:
            row["signals"]=[]
        p=dict(mode="synthetic",synthetic_fixture=True,initial_cash=1000,sessions=rows)
        with self.assertRaisesRegex(ValueError,"SIGNALS_REQUIRE"):
            replay(p)
        r=replay({**p,"missing_signal_policy":"hold"})
        self.assertEqual(r["trades"],[])
        self.assertEqual(r["curve"][-1]["equity"],1000)
