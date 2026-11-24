"""V3 isolated synthetic market data, no vendor prices and no live handoffs."""
from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest

from atlaspm.data import factor_db, factor_v3, market_db

try:
    import duckdb
except ImportError:
    duckdb = None


class V3Contracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.origin = Path.cwd()
        os.chdir(self.temp.name)
        Path("local_data").mkdir()

    def tearDown(self):
        os.chdir(self.origin)
        self.temp.cleanup()

    def test_reject_bad_inputs(self):
        base = {"start": "2026-01-05", "end": "2026-02-25"}
        for delta in (
            {"start": "2026-04-01"},
            {"factors": ["malicious ); DROP TABLE x; --"]},
            {"horizon": 2},
            {"weighting": "ic_train"},
            {"neutralize_industry": True},
            {"industry_csv": Path("local_data/test.csv")},
            {"winsor_tail": 0.25},
            {"redundancy_threshold": 0.1},
            {"min_symbols": 3},
            {"min_minute_fraction": -0.1},
            {"min_group_size": 1},
        ):
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                factor_v3.analyze(**(base | delta))

    def test_empty_source_refuses(self):
        with self.assertRaisesRegex(ValueError, "No materialized factor"):
            factor_v3.analyze(start="2026-01-05", end="2026-02-25")

    def test_embargo_filters_immature_training_returns(self):
        d = [
            {"date": "2026-01-08", "label_exit": "2026-01-09", "rank_ic": 0.2},
            {"date": "2026-01-09", "label_exit": "2026-01-13", "rank_ic": -0.5},
            {"date": "2026-01-13", "label_exit": "2026-01-15", "rank_ic": 0.4},
        ]
        periods = factor_v3._periods(d, {
            "holdout_start": "2026-01-12", "min_dates": 2
        })
        self.assertEqual(1, periods["train_observations_purged_due_to_label_overlap"])
        self.assertEqual(1, periods["train_matured"]["dates"])
        self.assertEqual(1, periods["holdout"]["dates"])
        self.assertEqual("INSUFFICIENT_DATED_IC", periods["train_matured"]["state"])

    def test_heldout_feature_availability_is_not_used_in_selection(self):
        c = {"factors":["mom_5d","mom_21d"],"min_dates":3,
             "holdout_start":"2026-02-01","redundancy_threshold":0.85}
        diags = [{"factor":name,"ic_stability":{"train_matured":{
                     "rank_ic_mean":None}}} for name in c["factors"]]
        coverage = [
            {"factor":"mom_5d","standardized_dates":40,
             "pre_holdout_standardized_dates":20},
            {"factor":"mom_21d","standardized_dates":40,
             "pre_holdout_standardized_dates":0},
        ]
        selected = factor_v3._select(c,diags,[],coverage)
        self.assertEqual(["mom_5d"],selected["selected_factors"])
        self.assertIn("mom_21d",selected["excluded_insufficient_standardized_dates"])

    def test_effective_dated_sector_map_rejects_overlap(self):
        path = Path("local_data/industry.csv")
        path.write_text(
            "symbol,industry,start_date,end_date\n"
            "TEST1,Tech,2026-01-01,2026-06-01\n"
            "TEST1,Energy,2026-05-31,2026-12-01\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            factor_v3._map_rows(path)
        with self.assertRaises(ValueError):
            factor_v3._map_rows(Path("../outside.csv"))


@unittest.skipUnless(duckdb, 'DuckDB required for V3 synthetic integration')
class V3DuckDB(V3Contracts):
    def _generate(self, *, source="hf", months=("2026-01", "2026-02", "2026-03")):
        days = []
        day = date(2026, 1, 1)
        while day <= date(2026, 3, 31):
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)
        cx = duckdb.connect()
        cols = (["symbol VARCHAR", "trade_date DATE", "close DOUBLE",
                 "observed_5m_bars BIGINT", "observed_1m_bars BIGINT"]
                + [f"{name} DOUBLE" for name in factor_db.FEATURES])
        cx.execute("CREATE TABLE synthetic (" + ",".join(cols) + ")")
        try:
            for month in months:
                values = []
                for n, day in enumerate(days):
                    if day.strftime("%Y-%m") != month:
                        continue
                    for rank in range(1, 9):
                        group = (rank-1) // 4
                        mom = float(10*group + rank)
                        features = {name: None for name in factor_db.FEATURES}
                        features.update(mom_5d=mom, intraday_range=-mom,
                                        mom_21d=float((rank*3+n) % 7),
                                        mom_63d=1.0)
                        values.append((
                            f"TEST{rank}", day.isoformat(),
                            100.0 * (1 + 0.002*rank)**n,
                            78 if source=="hf" else None,
                            390 if source=="hf" else None,
                            *[features[x] for x in factor_db.FEATURES]))
                cx.execute("DELETE FROM synthetic")
                cx.executemany("INSERT INTO synthetic VALUES (" +
                               ",".join(["?"]*len(cols)) + ")", values)
                p = factor_db._path(month, source, "rth", "factors")
                p.parent.mkdir(parents=True, exist_ok=True)
                cx.execute(f"COPY synthetic TO '{p.as_posix()}' (FORMAT PARQUET)")
                p.with_suffix(".json").write_text(json.dumps({
                    "version": factor_db.VERSION, "source": source,
                    "session": "rth", "month": month,
                    "source_price_adjustment": (
                        "UNVERIFIED_HF_RAW" if source=="hf"
                        else "SQLITE_ADJUSTED_CLOSE_VENDOR_UNVERIFIED"),
                    "approved_for_model_training": False,
                }), encoding="utf-8")
        finally:
            cx.close()

    def _industry(self, *, missing=False):
        path = Path("local_data/industry.csv")
        rows = ["symbol,industry,start_date,end_date"]
        upper = 7 if missing else 8
        for rank in range(1, upper+1):
            rows.append(f"TEST{rank},{'Tech' if rank<=4 else 'Energy'},"
                        "2026-01-01,2026-03-31")
        path.write_text("\n".join(rows)+"\n", encoding="utf-8")
        return path

    def _kwargs(self):
        return dict(start="2026-01-05", end="2026-02-25",
                    factors=["mom_5d", "intraday_range", "mom_21d", "mom_63d"],
                    horizon=1, min_symbols=5, min_dates=5,
                    memory="256MB", threads=1, save=False)

    def test_plan_is_no_scan_and_no_write(self):
        self._generate()
        res = factor_v3.analyze(**self._kwargs())
        self.assertEqual("plan_only", res["mode"])
        self.assertFalse(Path("local_data/factors/v3").exists())

    def test_correlation_redundancy_stability_and_equal_composite(self):
        self._generate()
        res = factor_v3.analyze(**self._kwargs(), exploratory=True)
        self.assertFalse(res["approved_for_model_training"])
        self.assertEqual("NOT_APPLIED_NO_DATED_MAPPING", res["industry_neutralization_state"])
        pair = next(p for p in res["factor_pair_correlations"]
                    if p["a"]=="mom_5d" and p["b"]=="intraday_range")
        self.assertAlmostEqual(-1, pair["mean_signed_spearman"], places=8)
        selected = res["redundancy_selection"]
        self.assertIn("mom_5d", selected["selected_factors"])
        self.assertNotIn("intraday_range", selected["selected_factors"])
        self.assertGreater(res["quality_funnel"]["quality_eligible_stock_day_rows"], 200)
        self.assertEqual("FIXED_EQUAL_POSITIVE", res["multi_factor"]["weighting_state"])
        self.assertGreater(res["multi_factor"]["composite"]["scored_dates"], 10)
        stable = {d["factor"]:d for d in res["factor_diagnostics"]}
        self.assertGreater(stable["mom_5d"]["ic_stability"]["all_descriptive"]["dates"], 10)
        self.assertEqual(0, next(c for c in res["factor_coverage"]
                                 if c["factor"]=="mom_63d")["standardized_rows"])

    def test_user_supplied_industry_mapping_is_required_complete(self):
        self._generate()
        with self.assertRaisesRegex(ValueError, "missing .* eligible"):
            factor_v3.analyze(**self._kwargs(), exploratory=True,
                neutralize_industry=True, industry_csv=self._industry(missing=True),
                min_group_size=2)
        res = factor_v3.analyze(**self._kwargs(), exploratory=True,
            neutralize_industry=True, industry_csv=self._industry(),
            min_group_size=2)
        self.assertTrue(res["industry_input_sha256"])
        self.assertTrue(res["industry_neutralization_state"].startswith("APPLIED"))
        self.assertFalse(res["industry_classifications_verified"])

    def test_train_ic_weights_and_holdout_purge(self):
        self._generate()
        kwargs = {**self._kwargs(), "factors": ["mom_5d", "intraday_range"],
                  "holdout_start": "2026-02-10", "weighting": "ic_train"}
        res = factor_v3.analyze(**kwargs, exploratory=True)
        composite = res["multi_factor"]
        self.assertEqual("MATURED_TRAINING_IC_SIGNED", composite["weighting_state"])
        self.assertEqual(1, len(composite["factor_weights"]))
        self.assertIsNotNone(composite["composite"]["ic_stability"]["holdout"])
        chosen = next(x for x in res["factor_diagnostics"] if x["factor"]=="mom_5d")
        self.assertGreaterEqual(
            chosen["ic_stability"]["train_observations_purged_due_to_label_overlap"], 1)

    def test_train_ic_is_fail_closed_without_matured_data(self):
        self._generate()
        kw = {**self._kwargs(), "factors": ["mom_5d"],
              "holdout_start": "2026-01-08", "weighting": "ic_train"}
        res = factor_v3.analyze(**kw, exploratory=True)
        self.assertEqual("INSUFFICIENT_MATURED_TRAINING_IC",
                         res["multi_factor"]["weighting_state"])
        self.assertIsNone(res["multi_factor"]["composite"])

    def test_sqlite_adjusted_source_kept_separate(self):
        self._generate(source="sqlite")
        kw = {**self._kwargs(), "factors":["mom_5d"],
              "source":"sqlite", "min_bars":78, "min_minute_fraction":1.0}
        res = factor_v3.analyze(**kw, exploratory=True)
        self.assertGreater(res["multi_factor"]["composite"]["scored_dates"], 10)
        self.assertNotIn("hf", res["source_price_adjustment"][0].lower())

    def test_save_report_and_research_only_score_file(self):
        self._generate()
        kw = {**self._kwargs(), "factors":["mom_5d"],
              "save":True, "save_scores":True}
        out = factor_v3.analyze(**kw, exploratory=True)
        self.assertTrue(Path(out["report_path"]).is_file())
        self.assertTrue(Path(out["research_only_scores_path"]).is_file())
        self.assertFalse(out["live_execution_enabled"])
        self.assertFalse(out["approved_for_model_training"])


if __name__=="__main__":
    unittest.main()
