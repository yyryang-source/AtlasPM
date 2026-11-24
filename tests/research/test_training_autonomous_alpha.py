import unittest
from copy import deepcopy
from datetime import date,timedelta
from importlib.util import find_spec

from atlaspm.research.autonomous_alpha import research, simulate
from atlaspm.research.alpha_metrics import evaluate, block_lower


def fixture():
    sessions=[]
    prices={s:100. for s in range(1,9)}
    for i in range(65):
        d=str(date(2020,1,1)+timedelta(days=i))
        t=d+"T21:00:00Z"
        assets=[]
        for s in prices:
            prices[s]*=1+.0004*s+(.0001 if i%2 else -.0001)
            assets.append(dict(security_id="SYN:"+str(s),source="synthetic",observed_at=t,
                               available_at=t,close=prices[s],
                               features=dict(momentum=s,volatility=9-s,liquidity=s%3)))
        sessions.append(dict(date=d,close_at=t,assets=assets))
    return dict(mode="synthetic",synthetic_fixture=True,feature_names=["momentum","volatility","liquidity"],
                sessions=sessions)


def options(p):
    return dict(validation_start=p["sessions"][25]["date"],holdout_start=p["sessions"][44]["date"])


class MetricTests(unittest.TestCase):
    def test_lag_and_ties(self):
        dates=["a","b","c"]
        rows=[dict(date=d,security_id=s,prediction=(1 if d=="a" and s=="A" else 0),
                   actual=(.1 if s=="A" else -.1)) for d in dates for s in ("A","B")]
        r=evaluate(rows,dates,cost_bps=0)
        self.assertAlmostEqual(r["mean_excess"],.05)
        self.assertEqual(r["sessions"],2)

    def test_block_bootstrap_deterministic(self):
        self.assertEqual(block_lower([.01]*20,trials=4),.01)
        self.assertIsNone(block_lower([.01]*3,trials=4))


@unittest.skipUnless(find_spec("numpy"),"portfolio extra required")
class AlphaTests(unittest.TestCase):
    def test_reproducible_and_no_live_permissions(self):
        p=fixture()
        a=research(p,**options(p))
        self.assertEqual(a,research(p,**options(p)))
        self.assertFalse(a["live_execution_enabled"])
        self.assertFalse(a["approved_for_model_training"])
        self.assertFalse(a["model_registry_admission"])
        self.assertIsNone(a["market_state_probability"])
        self.assertIn("rule:equal_weight",a["validation"])

    def test_holdout_never_changes_selection(self):
        p=fixture()
        a=research(p,**options(p))
        for session in p["sessions"][44:]:
            for item in session["assets"]:
                item["close"]*=1+int(item["security_id"].split(":")[1])*.1
        b=research(p,**options(p))
        self.assertEqual(a["selected_strategy"],b["selected_strategy"])
        self.assertEqual(a["validation"],b["validation"])
        self.assertEqual(a["factor_selection"],b["factor_selection"])

    def test_no_trade_and_corrupt_input(self):
        p=fixture()
        result=research(p,**options(p),cost_bps=900)
        self.assertEqual(result["decision"],"NO_TRADE")
        self.assertEqual(result["signals"],[])
        q=deepcopy(p)
        q["sessions"][0]["assets"][0]["available_at"]=q["sessions"][1]["close_at"]
        with self.assertRaisesRegex(ValueError,"FEATURE_LOOKAHEAD"):
            research(q,**options(q))
        q=deepcopy(p)
        q["mode"]="historical"
        with self.assertRaisesRegex(ValueError,"REAL_TRAINING_DATA_NOT_APPROVED"):
            research(q,**options(q))

    def test_permission_override(self):
        p=fixture()
        p["live_execution_enabled"]=True
        with self.assertRaisesRegex(ValueError,"ALPHA_PERMISSION_OVERRIDE"):
            research(p,**options(p))

    @unittest.skipUnless(find_spec("scipy"),"portfolio extra required")
    def test_real_paper_oms_adapter_with_synthetic_panel(self):
        import json
        from pathlib import Path
        p=fixture()
        # Exact synthetic identities, sectors and final marks match the existing
        # independently tested Unified Portfolio fixture.
        for row in p["sessions"]:
            row["assets"]=row["assets"][:2]
        last=p["sessions"][-1]["assets"]
        scales=[100/a["close"] for a in last]
        for row in p["sessions"]:
            for i,a in enumerate(row["assets"]):
                a["close"]*=scales[i]
                a["security_id"]="SYN:"+("AAA","BBB")[i]
                a["sector"]=("Technology","Healthcare")[i]
        portfolio=json.loads((Path(__file__).resolve().parents[2]/"examples"/"unified_portfolio_synthetic.json").read_text(encoding="utf-8"))
        portfolio["as_of"]=p["sessions"][-1]["close_at"]
        # Fixture history is separate from the synthetic alpha timeline; do not
        # pass historical portfolio performance into this contract smoke test.
        portfolio.pop("history",None)
        out=simulate(p,portfolio,**options(p),models=["historical_mean"],cost_bps=0)
        self.assertEqual(out["decision"],"SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE",out.get("portfolio"))
        self.assertTrue(all(out["portfolio"]["reconciliation_checks"].values()))
        self.assertFalse(out["live_execution_enabled"])
        self.assertEqual(out["research"]["holdout_candidates_evaluated"],[out["research"]["selected_strategy"]])
        portfolio["account"]["positions"][0]["mark_price"]=101
        with self.assertRaisesRegex(ValueError,"ALPHA_PORTFOLIO_MARK_MISMATCH"):
            simulate(p,portfolio,**options(p),models=["historical_mean"],cost_bps=0)

    def test_registry_lifecycle_and_corruption(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import sqlite3
        from contextlib import closing
        from atlaspm.research.alpha_lifecycle import history, transition
        from atlaspm.research.training_registry import load
        root=Path("local_data")
        root.mkdir(exist_ok=True)
        p=fixture()
        with TemporaryDirectory(dir=root) as tmp:
            db=Path(tmp)/"alpha.sqlite3"
            a=research(p,**options(p),models=["historical_mean"],cost_bps=0,registry_path=db)
            self.assertEqual(a["strategy_status"],"VALIDATED")
            version=a["strategy_version"]
            self.assertEqual(history(db,version)["state"],"VALIDATED")
            from atlaspm.research.alpha_lifecycle import compare_versions
            with self.assertRaisesRegex(ValueError,"DISTINCT_ALPHA"):
                compare_versions(db,version,version)
            challenger=research(p,**options(p),models=["historical_mean"],cost_bps=0,seed=43,registry_path=db)
            comparison=compare_versions(db,version,challenger["strategy_version"])
            self.assertTrue(comparison["comparable"])
            self.assertFalse(comparison["automatic_promotion_enabled"])
            self.assertFalse(comparison["holdout_reuse_for_model_selection_allowed"])
            self.assertEqual(load(db,a["registry_run_id"])["report"]["strategy_version"],version)
            with self.assertRaisesRegex(ValueError,"ALPHA_ONLY_DE_RISK"):
                transition(db,version,"PAPER_ACTIVE","untrusted promotion")
            transition(db,version,"SUSPENDED","manual review")
            b=research(p,**options(p),models=["historical_mean"],cost_bps=0,registry_path=db)
            self.assertEqual(b["decision"],"NO_TRADE")
            self.assertEqual(b["persistent_lifecycle_state"],"SUSPENDED")
            self.assertEqual(transition(db,version,"RETIRED","replaced")["state"],"RETIRED")
            # sqlite3's context manager commits/rolls back but does not close.
            with closing(sqlite3.connect(db)) as con, con:
                con.execute("UPDATE alpha_events SET event_sha='corrupt' WHERE sequence=1")
            with self.assertRaisesRegex(ValueError,"ALPHA_LIFECYCLE_INTEGRITY"):
                history(db,version)
            # Explicit deletion catches leaked SQLite handles on Windows.
            db.unlink()
            self.assertFalse(db.exists())

    def test_benchmark_reports_real_measurements(self):
        import json
        from atlaspm.research.alpha_benchmark import benchmark
        result=benchmark(65,8)
        self.assertGreater(result["elapsed_seconds"],0)
        self.assertGreater(result["peak_rss_bytes"],0)
        self.assertEqual(result["rows"],520)
        print("ALPHA_BENCHMARK "+json.dumps(result,sort_keys=True))
