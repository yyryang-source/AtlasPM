"""One integrated actual-fit to rank selection, optimizer and risk replay test."""
from copy import deepcopy
from importlib.util import find_spec
import unittest
from unittest.mock import patch

from atlaspm.research import alpha_multi_horizon as alpha
from atlaspm.research.alpha_replay_bridge import RankReplay
from .test_alpha_multi_horizon import panel, settings


def execution(p):
    rows=[]
    for i,s in enumerate(p["sessions"][60:],60):
        close={a["security_id"][4:]:a["close"] for a in s["assets"]}
        op={a["security_id"][4:]:a["close"] for a in p["sessions"][i-1]["assets"]}
        rows.append({"date":s["date"],"open_at":s["date"]+"T14:30:00Z","close_at":s["close_at"],
                     "source":"synthetic","open":op,"close":close,
                     "volume":{k:100000 for k in close},"sectors":{k:"fixture" for k in close}})
    return {"mode":"synthetic","synthetic_fixture":True,"sessions":rows,
            "initial_cash":10000,"cash_reserve":.2,"top_k":1,"rebalance_sessions":1,
            "fee_bps":1,"slippage_bps":1,"settlement_sessions":1,
            "execution_policy":{"max_participation":.01,"impact_bps_at_full_participation":10,
                                "fill_fraction":1},
            "risk_policy":{"max_drawdown":.25,"max_gross_leverage":1.,
                           "equity_shock":.2,"max_stress_loss_fraction":.25},
            "position_lifecycle":{"stop_loss":.05,"take_profit":.3,"max_holding_sessions":20}}


WEIGHTS={"1":1,"5":1,"21":1}


class RankReplayTests(unittest.TestCase):
    @unittest.skipUnless(find_spec("numpy") and find_spec("scipy"),"portfolio extras required")
    def test_actual_locked_training_to_replay(self):
        p=panel()
        marks=execution(p)
        marks["optimizer"]={"method":"equal_weight","lookback_sessions":3,"max_name_weight":1.0}
        before=deepcopy(marks)
        out=alpha.research(p,**settings(),replay_payload=marks,rank_weights=WEIGHTS)
        r=out["synthetic_rank_replay"]
        sim=r["simulation"]
        self.assertEqual(marks,before)
        self.assertEqual(r["selection_lock_sha256"],out["selection_lock_sha256"])
        self.assertTrue(any(c["signals"] for c in r["cohorts"]))
        self.assertTrue(sim["trades"])
        self.assertTrue(sim["allocations"])
        self.assertTrue(all(x["estimation_last_date"]==x["signal_date"] for x in sim["allocations"]))
        self.assertTrue(all(t["date"]>t["signal_date"] for t in sim["trades"]))
        self.assertTrue(sim["risk_audit"])
        self.assertAlmostEqual(sim["curve"][-1]["equity"]-marks["initial_cash"],
            sum(x["overnight_pnl"]+x["intraday_pnl"]-x["trade_cost"] for x in sim["curve"]),places=6)
        self.assertFalse(r["broker_order_submission_enabled"])
        self.assertFalse(r["approved_for_model_training"])
        self.assertFalse(r["approved_for_backtest"])
        self.assertEqual(out["signals"],[])

    def test_coverage_ties_and_labels_never_used_for_ranking(self):
        p=panel()
        b=RankReplay(p,60,(1,5,21),WEIGHTS,execution(p))
        first,second,third=b.common_days[:3]
        for h in (1,5,21):
            rows=[]
            for day in (first,second,third):
                for i,sid in enumerate(b.ids):
                    if day==second and h==21 and i==0:
                        continue
                    rows.append({"date":day,"security_id":sid,
                                 "prediction":0 if day==third else i,
                                 "actual":None})  # no label filtering
            b.add(h,rows)
        with patch("atlaspm.research.alpha_replay_bridge.replay",return_value={}) as run:
            r=b.finish("a"*64)
        self.assertEqual([c["status"] for c in r["cohorts"][:3]],
                         ["SYNTHETIC_RANK_COHORT","INCOMPLETE_FORECAST_COHORT","TIED_SELECTION_BOUNDARY"])
        signals=run.call_args.args[0]["sessions"][0]["signals"]
        self.assertEqual(signals[0]["security_id"],b.ids[-1])
        self.assertEqual(run.call_args.args[0]["missing_signal_policy"],"hold")
        self.assertEqual(run.call_args.args[0]["sessions"][1]["signals"],[])

    def test_reject_mismatched_marks_and_permissions_before_fit(self):
        p=panel()
        for mutate in (
            lambda m:m.update(live_execution_enabled=True),
            lambda m:m.update(margin_policy={}),
            lambda m:m["sessions"][0]["close"].update({"1":999}),
            lambda m:m["sessions"][0].update(signals=[]),
            lambda m:m["position_lifecycle"].update(thesis_score_floor=0),
        ):
            m=execution(p)
            mutate(m)
            with patch.object(alpha.tournament,"train") as train:
                with self.assertRaises(ValueError):
                    alpha.research(p,**settings(),replay_payload=m,rank_weights=WEIGHTS)
                train.assert_not_called()

    def test_fixed_weight_and_universe_guard(self):
        p=panel()
        m=execution(p)
        with self.assertRaisesRegex(ValueError,"EXACT_HORIZON_WEIGHTS"):
            RankReplay(p,60,(1,5,21),{"1":1},m)
        p["sessions"][61]["assets"].pop()
        with self.assertRaisesRegex(ValueError,"FIXED_UNIVERSE"):
            RankReplay(p,60,(1,5,21),WEIGHTS,m)
