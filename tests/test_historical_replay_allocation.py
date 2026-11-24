import unittest
from datetime import date, timedelta

from atlaspm.engine.replay import replay
from atlaspm.engine.allocation import METHODS
from test_historical_replay import fixture


def rolling_fixture(method):
    p = fixture()
    rows = []
    from copy import deepcopy
    for i in range(28):
        row = deepcopy(p["sessions"][0])
        d = str(date(2020,1,2)+timedelta(days=i))
        row["date"] = d
        row["open_at"] = d+"T09:30:00-05:00"
        row["close_at"] = d+"T16:00:00-05:00"
        row["open"] = {"AAA":100+i+(i%3), "BBB":100+i*.5+(i%4)}
        row["close"] = dict(row["open"])
        for s in row["signals"]:
            for key in ("generated_at","available_at","data_available_at","expires_at"):
                s[key] = row["close_at"]
        rows.append(row)
    p.update(sessions=rows,top_k=2,cash_reserve=.2,rebalance_sessions=5,
             optimizer=dict(method=method,lookback_sessions=20,max_name_weight=.7,
                            risk_budgets={"AAA":1,"BBB":1}))
    return p


class RollingAllocationTests(unittest.TestCase):
    def test_all_methods(self):
        for method in sorted(METHODS):
            with self.subTest(method=method):
                out = replay(rolling_fixture(method))
                self.assertTrue(out["allocations"])
                for a in out["allocations"]:
                    self.assertEqual(a["signal_date"],a["estimation_last_date"])
                    self.assertEqual(a["return_observations"],20)
                    self.assertAlmostEqual(sum(a["weights"].values()),.8,places=5)
                self.assertTrue(all(x["signal_date"]<x["date"] for x in out["trades"]))
                self.assertFalse(out["approved_for_model_training"])

    def test_prefix_invariance(self):
        p = rolling_fixture("equal_weight")
        full = replay(p)
        p["sessions"] = p["sessions"][:24]
        short = replay(p)
        self.assertEqual(full["curve"][:24],short["curve"])
        self.assertEqual(full["allocations"][:1],short["allocations"])

    def test_no_trades_during_warmup(self):
        out = replay(rolling_fixture("equal_weight"))
        self.assertTrue(all(not x["holdings"] for x in out["curve"][:21]))

    def test_leverage_rejected(self):
        p = rolling_fixture("equal_weight")
        p["optimizer"]["max_gross_exposure"] = 2
        with self.assertRaises(ValueError):
            replay(p)


if __name__ == "__main__":
    unittest.main()
