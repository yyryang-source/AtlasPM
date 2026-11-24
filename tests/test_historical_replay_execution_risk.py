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
