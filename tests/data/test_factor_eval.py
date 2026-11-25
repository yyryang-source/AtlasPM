"""Synthetic, offline V2 diagnostics: no vendor data, no trading permissions."""
from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest

from atlaspm.data import factor_db, factor_eval

try:
    import duckdb
except ImportError:
    duckdb = None


class FactorEvaluationContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path.cwd()
        os.chdir(self.tmp.name)
        Path("local_data").mkdir()

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_invalid_inputs(self):
        common = {"start": "2026-01-01", "end": "2026-03-31"}
        for updates in (
            {"factors": ["__import__('os')"]}, {"horizons": [2]},
            {"start": "2026-04-01"}, {"min_symbols": 4},
            {"min_minute_fraction": 1.5},
            {"holdout_start": "2025-01-01"}, {"cost_bps_per_leg_roundtrip": -1}
        ):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                factor_eval.evaluate(**(common | updates))

    def test_missing_factor_files_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "No materialized"):
            factor_eval.evaluate(start="2026-01-01", end="2026-02-28")


@unittest.skipUnless(duckdb, 'DuckDB required for synthetic Parquet evaluation')
class FactorEvaluationIntegration(FactorEvaluationContracts):
    def _write(self, *, months=("2026-01", "2026-02", "2026-03"),
               source="hf", flat=False):
        sessions = []
        day = date(2026, 1, 1)
        while day <= date(2026, 3, 31):
            if day.weekday() < 5:
                sessions.append(day)
            day += timedelta(days=1)
        con = duckdb.connect()
        columns = (["symbol VARCHAR", "trade_date DATE", "close DOUBLE",
                    "observed_5m_bars BIGINT", "observed_1m_bars BIGINT"]
                   + [f"{name} DOUBLE" for name in factor_db.FEATURES])
        con.execute(f"CREATE TABLE bars ({','.join(columns)})")
        try:
            for month in months:
                rows = []
                for idx, day in enumerate(sessions):
                    if day.strftime("%Y-%m") != month:
                        continue
                    for rank in range(1, 7):
                        close = 100.0 if flat else 100.0 * (1 + rank * 0.004) ** idx
                        vals = {name: None for name in factor_db.FEATURES}
                        vals["mom_5d"] = float(rank)
                        vals["intraday_range"] = float(7 - rank)
                        vals["mom_21d"] = 1.0  # Constant-factor has undefined IC.
                        rows.append((f"TEST{rank}", day.isoformat(), close,
                                     78 if source == "hf" else None,
                                     390 if source == "hf" else None,
                                     *[vals[n] for n in factor_db.FEATURES]))
                con.execute("DELETE FROM bars")
                con.executemany(f"INSERT INTO bars VALUES ({','.join(['?'] * len(columns))})", rows)
                path = factor_db._path(month, source, "rth", "factors")
                path.parent.mkdir(parents=True, exist_ok=True)
                con.execute(f"COPY bars TO '{path.as_posix()}' (FORMAT PARQUET)")
                path.with_suffix(".json").write_text(json.dumps({
                    "version": factor_db.VERSION, "month": month, "source": source,
                    "session": "rth", "source_price_adjustment": "UNVERIFIED_HF_RAW",
                    "approved_for_model_training": False,
                }), encoding="utf-8")
        finally:
            con.close()

    def test_exploratory_opt_in_and_no_future_leakage(self):
        self._write()
        kwargs = dict(start="2026-01-05", end="2026-02-20",
                      factors=["mom_5d", "intraday_range", "mom_21d"],
                      horizons=[1, 5], min_symbols=5, min_dates=3,
                      holdout_start="2026-02-02",
                      memory="256MB", threads=1, save=False)
        plan = factor_eval.evaluate(**kwargs)
        self.assertEqual("plan_only", plan["mode"])
        self.assertFalse(Path("local_data/factors/v2").exists())
        res = factor_eval.evaluate(**kwargs, exploratory=True)
        self.assertFalse(res["approved_for_model_training"])
        self.assertFalse(res["live_execution_enabled"])
        by_name = {(r["factor"],r["horizon_sessions"]): r for r in res["factor_horizon_results"]}
        for horizon in (1, 5):
            pos = by_name["mom_5d", horizon]["full_period"]
            neg = by_name["intraday_range", horizon]["full_period"]
            self.assertEqual("EXPLORATORY_UNVERIFIED", pos["state"])
            self.assertAlmostEqual(1.0, pos["rank_ic_mean"], places=10)
            self.assertAlmostEqual(-1.0, neg["rank_ic_mean"], places=10)
            self.assertGreater(pos["q5_minus_q1_mean"], 0)
        constant = by_name["mom_21d", 1]["full_period"]
        self.assertEqual("INSUFFICIENT_DATED_IC", constant["state"])
        self.assertIsNone(constant["rank_ic_mean"])
        self.assertIsNotNone(by_name["mom_5d", 1]["chronological_diagnostic"]["holdout"])

    def test_materialized_labels_preserve_pairwise_factor_missingness(self):
        self._write()
        cfg=factor_eval._config(start="2026-01-05",end="2026-02-20",
            source="hf",session="rth",factors=["mom_5d","intraday_range"],
            horizons=[1,5],holdout_start=None,min_symbols=5,min_dates=2,
            min_bars=60,min_minute_fraction=.85,cost_bps_per_leg_roundtrip=20)
        con=factor_db._db("256MB",1)
        try:
            paths,_=factor_eval._inputs(cfg)
            con.execute("CREATE TEMP TABLE eval_prices AS SELECT * FROM "+factor_db._files_sql(paths))
            con.execute("UPDATE eval_prices SET mom_5d=NULL WHERE symbol='TEST1'")
            con.execute("UPDATE eval_prices SET observed_1m_bars=0 WHERE symbol='TEST2' AND trade_date=DATE '2026-01-09'")
            for h in (1,5):
                factor_eval._materialize_forward_labels(con,h,cfg,version="v2")
                for name in cfg["factors"]:
                    direct=con.execute(factor_eval._dated_sql(name,h,cfg),[cfg["start"],cfg["end"]]).fetchall()
                    cached=con.execute(factor_eval._dated_sql(name,h,cfg,materialized=True)).fetchall()
                    self.assertEqual(len(direct),len(cached))
                    for x,y in zip(direct,cached):
                        self.assertEqual(x[:2],y[:2])
                        for a,b in zip(x[2:],y[2:]):
                            if a is None or b is None:
                                self.assertIs(a,b)
                            else:
                                self.assertAlmostEqual(a,b,places=12)
        finally:
            con.close()

    def test_unsupported_source_metadata_is_rejected(self):
        self._write(months=("2026-01",))
        path = factor_db._path("2026-01", "hf", "rth", "factors")
        meta = json.loads(path.with_suffix(".json").read_text())
        meta["approved_for_model_training"] = True
        path.with_suffix(".json").write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "training flag"):
            factor_eval.evaluate(start="2026-01-05", end="2026-01-20")

    def test_sparse_month_gap_never_links_distant_dates(self):
        self._write(months=("2026-01", "2026-03"))
        result = factor_eval.evaluate(start="2026-01-26", end="2026-01-30",
                                      factors=["mom_5d"], horizons=[1],
                                      min_symbols=5, min_dates=2,
                                      exploratory=True, save=False,
                                      memory="256MB", threads=1)
        dated = result["factor_horizon_results"][0]["daily_ic"]
        self.assertTrue(all(r["date"] < "2026-01-29" for r in dated))

    def test_missing_future_labels_do_not_get_fabricated(self):
        self._write(months=("2026-01",))
        result = factor_eval.evaluate(start="2026-01-29", end="2026-01-30",
                                      factors=["mom_5d"], horizons=[5],
                                      min_symbols=5, min_dates=2,
                                      exploratory=True, save=False,
                                      memory="256MB", threads=1)
        out = result["factor_horizon_results"][0]
        self.assertEqual(0, out["dated_cohorts_found"])
        self.assertEqual("INSUFFICIENT_DATED_IC", out["full_period"]["state"])

    def test_sqlite_source_without_intraday_bar_requirement(self):
        self._write(months=("2026-01", "2026-02"), source="sqlite")
        result = factor_eval.evaluate(start="2026-01-05", end="2026-02-18",
                                      source="sqlite", factors=["mom_5d"], horizons=[1],
                                      min_symbols=5, min_dates=3,
                                      exploratory=True, save=False,
                                      memory="256MB", threads=1)
        self.assertGreater(result["factor_horizon_results"][0]["full_period"]["dates"], 3)


if __name__ == "__main__":
    unittest.main()
