import copy
import unittest

from atlaspm.engine.replay import replay


def fixture():
    rows = []
    for day, price, score in [("2020-01-02", 100, 2), ("2020-01-03", 110, -2),
                              ("2020-01-06", 120, -2), ("2020-01-07", 130, -2)]:
        close = day+"T16:00:00-05:00"
        signals = [dict(symbol=s, security_id="SYN:"+s, sector="test", model_version="fixture",
                        identity_source="synthetic", data_source="synthetic",
                        data_quality="synthetic_test_only", research_eligible=True,
                        model_approved_for_training=False, generated_at=close,
                        available_at=close, data_available_at=close, expires_at=close,
                        factor_score=v, model_rank=i+1)
                   for i,(s,v) in enumerate([("AAA",score),("BBB",0)])]
        rows.append(dict(date=day, open_at=day+"T09:30:00-05:00", close_at=close,
                         source="synthetic", open={"AAA":price,"BBB":100},
                         close={"AAA":price,"BBB":100}, signals=signals))
    return dict(mode="synthetic", synthetic_fixture=True, initial_cash=1000,
                fee_bps=0, slippage_bps=0, cash_reserve=0, top_k=1,
                rebalance_sessions=1, settlement_sessions=1, sessions=rows)


class ReplayTests(unittest.TestCase):
    def test_next_open_and_settlement(self):
        out = replay(fixture())
        self.assertEqual(out["curve"][0]["holdings"], {})
        self.assertEqual(out["curve"][1]["holdings"], {"AAA":9})
        self.assertEqual(out["curve"][2]["unsettled_cash"], 1080)
        self.assertEqual(out["curve"][2]["holdings"], {})
        self.assertEqual(out["curve"][3]["holdings"], {"BBB":10})
        self.assertEqual(out["curve"][3]["equity"], 1090)
        self.assertFalse(out["live_execution_enabled"])

    def test_future_signal_refused(self):
        p = fixture()
        p["sessions"][0]["signals"][0]["available_at"] = "2020-01-03T16:00:00-05:00"
        with self.assertRaises(ValueError):
            replay(p)

    def test_prefix_invariance(self):
        p = fixture()
        full = replay(p)
        p["sessions"] = p["sessions"][:2]
        short = replay(p)
        self.assertEqual(full["curve"][:2], short["curve"])

    def test_missing_held_mark_refused(self):
        p = fixture()
        for key in ("open","close"):
            del p["sessions"][2][key]["AAA"]
        with self.assertRaises(ValueError):
            replay(p)

    def test_cost_reconciles(self):
        p = fixture()
        p["fee_bps"] = 10
        p["slippage_bps"] = 20
        out = replay(p)
        self.assertGreater(out["total_cost"], 0)
        self.assertAlmostEqual(out["curve"][-1]["equity"]-1000,
            sum(x["overnight_pnl"]+x["intraday_pnl"]-x["trade_cost"] for x in out["curve"]))
        self.assertTrue(all(x["settled_cash"] >= 0 for x in out["curve"]))

    def test_real_data_blocked(self):
        p = fixture()
        p["mode"] = "historical"
        with self.assertRaises(ValueError):
            replay(p)

    def test_duplicate_session_refused(self):
        p = fixture()
        p["sessions"][1] = copy.deepcopy(p["sessions"][0])
        with self.assertRaises(ValueError):
            replay(p)


if __name__ == "__main__":
    unittest.main()
