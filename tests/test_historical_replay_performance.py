import unittest
from importlib.util import find_spec

from atlaspm.engine.replay_performance import compare, metrics
from test_historical_replay_allocation import rolling_fixture


class MetricsTests(unittest.TestCase):
    def test_identical_benchmark(self):
        x = metrics([.1,-.1,.02],[.1,-.1,.02])
        self.assertAlmostEqual(x["beta"],1)
        self.assertAlmostEqual(x["alpha_annual_arithmetic"],0)
        self.assertAlmostEqual(x["max_drawdown"],-.1)
        self.assertIsNone(x["information_ratio"])

    def test_flat_benchmark(self):
        self.assertIsNone(metrics([0,0],[0,0])["beta"])


@unittest.skipUnless(find_spec("numpy") and find_spec("scipy"), "portfolio extra required")
class ComparisonTests(unittest.TestCase):
    def sample(self):
        p=rolling_fixture("equal_weight")
        p["benchmark_basket"]=["AAA","BBB"]
        for i,row in enumerate(p["sessions"]):
            row["benchmark_close"]={"SPY":100+i,"QQQ":100+i*2}
        return p

    def test_all_methods(self):
        out=compare(self.sample())
        self.assertEqual(len(out["strategies"]),7)
        self.assertEqual(len(out["dates"]),8)
        for k,v in out["strategies"].items():
            self.assertEqual(v["observations"],7)
            self.assertAlmostEqual(v["total_return"],out["strategy_equity_indices"][k][-1]-1)
        self.assertFalse(out["live_execution_enabled"])

    def test_missing_benchmark(self):
        p=self.sample()
        del p["sessions"][-1]["benchmark_close"]
        with self.assertRaisesRegex(ValueError,"BENCHMARK_MARK"):
            compare(p,["equal_weight"])


if __name__=="__main__":
    unittest.main()
