import unittest
from copy import deepcopy

from atlaspm.engine.replay import replay
from atlaspm.engine.replay_margin import margin_stress
from test_historical_replay import fixture


class ExecutionTests(unittest.TestCase):
    def sample(self):
        p=fixture()
        p["execution_policy"]={"max_participation":.1,"impact_bps_at_full_participation":50}
        for row in p["sessions"]:
            row["volume"]={"AAA":30,"BBB":30}
        return p

    def test_partial_and_audit(self):
        out=replay(self.sample())
        order=out["orders"][0]
        self.assertEqual(order["requested"],9)
        self.assertEqual(order["filled"],3)
        self.assertEqual(order["status"],"CANCELED")
        self.assertEqual(len(order["audit_root_sha256"]),64)
        self.assertTrue(all(x["settled_cash"]>=0 for x in out["curve"]))
        self.assertAlmostEqual(out["curve"][-1]["equity"]-1000,
            sum(x["overnight_pnl"]+x["intraday_pnl"]-x["trade_cost"] for x in out["curve"]))

    def test_no_same_day_volume_leak(self):
        p=self.sample()
        first=replay(p)["orders"][0]
        p["sessions"][1]["volume"]["AAA"]=1000000
        self.assertEqual(first,replay(p)["orders"][0])

    def test_zero_volume_reject(self):
        p=self.sample()
        p["sessions"][0]["volume"]["AAA"]=0
        order=replay(p)["orders"][0]
        self.assertEqual(order["status"],"REJECTED")
        self.assertEqual(order["filled"],0)

    def test_missing_volume_fails(self):
        p=self.sample()
        del p["sessions"][0]["volume"]
        with self.assertRaises(ValueError):
            replay(p)


class PositionExitTests(unittest.TestCase):
    def case(self):
        p=fixture()
        p.update(top_k=2,rebalance_sessions=1,risk_policy={},
                 position_lifecycle={"stop_loss":.3,"take_profit":2.,
                    "max_holding_sessions":20,"exit_scope":"position","thesis_score_floor":-1.})
        # BBB must remain open even though AAA loses its thesis at close 2.
        return p

    def test_position_exit_does_not_flatten_other_holdings(self):
        p=self.case()
        out=replay(p)
        self.assertEqual(out["curve"][1]["holdings"],{"AAA":4,"BBB":5})
        self.assertEqual(out["curve"][2]["holdings"],{"BBB":5})
        self.assertFalse(out["risk_halt_latched"])
        self.assertEqual(out["position_exit_latches"]["AAA"]["kind"],"POSITION_THESIS_INVALIDATED")
        self.assertEqual(out["remaining_position_exits"],{})
        self.assertFalse(out["position_reentry_enabled"])
        self.assertFalse(out["broker_order_submission_enabled"])

    def test_partial_exit_retries_despite_fresh_positive_signal(self):
        p=self.case()
        p["execution_policy"]={"max_participation":.1,"impact_bps_at_full_participation":0}
        for row in p["sessions"]:
            row["volume"]={"AAA":100,"BBB":100}
        p["sessions"][1]["volume"]["AAA"]=10
        p["sessions"][2]["signals"][0]["factor_score"]=5.
        out=replay(p)
        self.assertEqual(out["curve"][2]["holdings"],{"AAA":3,"BBB":5})
        self.assertEqual(out["curve"][3]["holdings"],{"BBB":5})
        self.assertEqual([e["kind"] for e in out["position_lifecycle_audit"] if e["symbol"]=="AAA"],
                         ["POSITION_THESIS_INVALIDATED","POSITION_EXIT_PARTIAL","POSITION_EXIT_FILLED"])
        self.assertFalse(out["risk_halt_latched"])

    def test_trailing_exit_uses_completed_close_and_preserves_prefix(self):
        p=self.case()
        p["rebalance_sessions"]=10
        del p["position_lifecycle"]["thesis_score_floor"]
        p["position_lifecycle"]["trailing_stop"]=.1
        p["sessions"][1]["close"]["AAA"]=150
        p["sessions"][2]["close"]["AAA"]=120
        out=replay(p)
        self.assertEqual(out["position_exit_latches"]["AAA"]["kind"],"POSITION_TRAILING_STOP")
        self.assertEqual(out["curve"][2]["holdings"],{"AAA":4,"BBB":5})
        self.assertEqual(out["curve"][3]["holdings"],{"BBB":5})
        p["sessions"]=p["sessions"][:3]
        short=replay(p)
        self.assertEqual(out["curve"][:3],short["curve"])
        self.assertEqual(short["remaining_position_exits"],{"AAA":4})


class MarginTests(unittest.TestCase):
    def test_weekend_interest(self):
        rows=[{"date":"2020-01-03","synthetic_index":100,"source":"synthetic"},
              {"date":"2020-01-06","synthetic_index":100,"source":"synthetic"}]
        out=margin_stress(rows,initial_equity=1000,leverage=2,annual_rate=.10,maintenance_fraction=.3)
        self.assertAlmostEqual(out["total_interest"],1000*.1*3/365)
        self.assertFalse(out["broker_margin_verified"])

    def test_breach_and_insolvency(self):
        rows=[{"date":d,"synthetic_index":v,"source":"synthetic"}
              for d,v in [("2020-01-02",100),("2020-01-03",85),("2020-01-06",60)]]
        out=margin_stress(rows,initial_equity=1000,leverage=3,annual_rate=0,maintenance_fraction=.3)
        self.assertEqual(out["first_breach"],"2020-01-03")
        self.assertTrue(out["terminated_insolvent"])

    def test_unlevered_no_interest(self):
        rows=[{"date":d,"synthetic_index":v,"source":"synthetic"}
              for d,v in [("2020-01-02",100),("2020-01-03",50)]]
        out=margin_stress(rows,initial_equity=1000,leverage=1,annual_rate=.1,maintenance_fraction=.3)
        self.assertEqual(out["total_interest"],0)
        self.assertEqual(out["path"][-1]["net_liquidation"],500)
        self.assertIsNone(out["first_breach"])
