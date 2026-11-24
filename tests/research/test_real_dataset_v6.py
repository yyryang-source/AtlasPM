"""V6 research sample registry and exact-session label tests."""
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.research import real_dataset_v6 as v
from atlaspm.research.training_dataset import validate_panel_metadata

CAL=[("2020-01-30",0,600000),("2020-01-31",600000,1200000),
     ("2020-02-03",1200000,1800000),("2020-02-04",1800000,2400000),
     ("2020-02-05",2400000,3000000),("2020-02-06",3000000,3600000)]


class RealDatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        old=Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir,old)
        Path("local_data").mkdir()

    def seed(self,duplicate=False):
        try:
            import duckdb
        except ImportError:
            self.skipTest("Install lake extras")
        cx=duckdb.connect()
        try:
            for month in ("2020-01","2020-02"):
                p=v.factor._path(month,"hf","rth","factors")
                p.parent.mkdir(parents=True,exist_ok=True)
                cx.execute("""CREATE OR REPLACE TABLE fixture(symbol VARCHAR,trade_date DATE,
                    close DOUBLE,last_observed_bar_ms BIGINT,mom_5d DOUBLE)""")
                rows=[]
                for i,(d,o,c) in enumerate(CAL):
                    if not d.startswith(month):
                        continue
                    rows.append(("ABC",d,10+i,c-300000,.1 if i else None))
                    if i!=2:
                        rows.append(("GAP",d,20+i,c-300000,.2))
                if duplicate:
                    rows.append(rows[0])
                cx.executemany("INSERT INTO fixture VALUES (?,?,?,?,?)",rows)
                cx.execute(f"COPY fixture TO {v.factor._quoted(p)} (FORMAT PARQUET)")
                p.with_suffix(".json").write_text(json.dumps({"version":v.factor.VERSION,
                    "month":month,"source":"hf","session":"rth"}),encoding="utf-8")
        finally:
            cx.close()

    def build(self):
        with patch.object(v,"_calendar",return_value=CAL):
            return v.build("2020-01","2020-02",features=["mom_5d"],horizons=[1,5],
                           minimum_train_sessions=1,test_sessions=1,embargo_sessions=1)

    def test_missing_plan_no_training(self):
        r=v.build("2020-01","2020-02",plan_only=True)
        self.assertEqual(r["missing_factor_months"],["2020-01","2020-02"])
        self.assertFalse(r["approved_for_model_training"])
        self.assertFalse(r["synthetic_fixture"])

    def test_calendar_labels_no_bridge_and_cross_month(self):
        self.seed()
        r=self.build()
        self.assertEqual(r["rows"],11)
        self.assertFalse(r["approved_for_model_training"])
        self.assertFalse(r["live_execution_enabled"])
        cx=v.factor._db("512MB",1)
        try:
            src=v.factor._files_sql([Path(x["path"]) for x in r["partitions"]])
            a=cx.execute(f"""SELECT label_1d,label_end_index_1d FROM {src}
                          WHERE symbol='ABC' AND trade_date='2020-01-31'""").fetchone()
            self.assertAlmostEqual(a[0],12/11-1)
            self.assertEqual(a[1],2)
            self.assertIsNone(cx.execute(f"""SELECT label_1d FROM {src}
                          WHERE symbol='GAP' AND trade_date='2020-01-31'""").fetchone()[0])
            self.assertIsNone(cx.execute(f"""SELECT label_5d FROM {src}
                          WHERE symbol='GAP' AND trade_date='2020-01-30'""").fetchone()[0])
            self.assertIsNone(cx.execute(f"""SELECT label_1d FROM {src}
                          WHERE symbol='ABC' AND trade_date='2020-02-06'""").fetchone()[0])
        finally:
            cx.close()
        self.assertEqual(v.inspect(r["dataset_id"])["dataset_id"],r["dataset_id"])
        self.assertEqual(self.build()["dataset_id"],r["dataset_id"])
        with self.assertRaisesRegex(ValueError,"REAL_TRAINING_DATA_NOT_APPROVED"):
            validate_panel_metadata(r,1,1)

    def test_duplicate_partition_blocks_publication(self):
        self.seed(duplicate=True)
        with self.assertRaisesRegex(ValueError,"duplicates=True"):
            self.build()
        self.assertFalse((v.BASE/"latest.json").exists())

    def test_corruption_detected_and_rebuilt(self):
        self.seed()
        r=self.build()
        p=Path(r["partitions"][0]["path"])
        p.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError,"integrity"):
            v.inspect(r["dataset_id"])
        rebuilt=self.build()
        self.assertEqual(rebuilt["dataset_id"],r["dataset_id"])
        self.assertEqual(v.inspect(r["dataset_id"])["rows"],11)

    def test_purged_folds(self):
        folds=v._folds(CAL,1,1,1,1)
        self.assertEqual(len(folds),2)
        for f in folds:
            self.assertLess(f["latest_training_label_end_index"],f["test_start_index"]-1)
            self.assertEqual(f["train_end_index"]+1,f["latest_training_label_end_index"])
        self.assertEqual(v._folds(CAL,5,1,1,1),[])

    def test_configuration_rejects_feature_injection(self):
        for features in (["label_1d"],["mom_5d","mom_5d"],["close"]):
            with self.assertRaises(ValueError):
                v.build("2020-01","2020-02",features=features)
        with self.assertRaises(ValueError):
            v.build("2020-01","2020-02",horizons=[2])


    def test_preflight_counts_and_no_training(self):
        from atlaspm.research import real_dataset_preflight_v61 as pre
        self.seed()
        r=self.build()
        with patch.object(v,"_calendar",return_value=CAL):
            report=pre.run(r["dataset_id"],minimum_train_rows=1,minimum_test_rows=1)
        self.assertEqual(report["observed_rows"],11)
        self.assertEqual(report["feature_complete_rows"],10)
        self.assertTrue(report["label_paths_reconciled"])
        self.assertEqual(report["horizons"]["1"]["fold_count"],2)
        self.assertEqual(report["horizons"]["5"]["fold_count"],0)
        self.assertEqual(report["models_fitted"],0)
        self.assertFalse(report["approved_for_model_training"])
        self.assertFalse(report["live_execution_enabled"])

    def test_preflight_rejects_rehashed_incorrect_label(self):
        from atlaspm.research import real_dataset_preflight_v61 as pre
        self.seed()
        r=self.build()
        p=Path(r["partitions"][0]["path"])
        stage=p.with_suffix(".part")
        cx=v.factor._db("512MB",1)
        try:
            src=v.factor._files_sql([p])
            cx.execute(f"""COPY (SELECT * REPLACE (
                CASE WHEN label_1d IS NULL THEN NULL ELSE label_1d+0.1 END AS label_1d
                ) FROM {src}) TO {v.factor._quoted(stage)} (FORMAT PARQUET)""")
        finally:
            cx.close()
        stage.replace(p)
        r["partitions"][0]["sha256"]=v._stamp(p)["sha256"]
        Path(r["manifest_path"]).write_text(json.dumps(r),encoding="utf-8")
        with patch.object(v,"_calendar",return_value=CAL):
            with self.assertRaisesRegex(ValueError,"Label path reconciliation failed"):
                pre.run(r["dataset_id"])

    def test_preflight_detects_calendar_change(self):
        from atlaspm.research import real_dataset_preflight_v61 as pre
        self.seed()
        r=self.build()
        with patch.object(v,"_calendar",return_value=CAL[:-1]):
            with self.assertRaisesRegex(ValueError,"Calendar has changed"):
                pre.run(r["dataset_id"])

    def test_source_revision_and_rehashed_features_block_preflight(self):
        from atlaspm.research import real_dataset_preflight_v61 as pre
        self.seed()
        r=self.build()
        p=Path(r["partitions"][0]["path"])
        stage=p.with_suffix(".part")
        cx=v.factor._db("512MB",1)
        try:
            src=v.factor._files_sql([p])
            cx.execute(f" COPY (SELECT * REPLACE (mom_5d+0.01 AS mom_5d) FROM {src}) TO {v.factor._quoted(stage)} (FORMAT PARQUET)")
        finally:
            cx.close()
        stage.replace(p)
        r["partitions"][0]["sha256"]=v._stamp(p)["sha256"]
        Path(r["manifest_path"]).write_text(json.dumps(r),encoding="utf-8")
        with patch.object(v,"_calendar",return_value=CAL):
            with self.assertRaisesRegex(ValueError,"feature/source reconciliation failed"):
                pre.run(r["dataset_id"])
        source=v.factor._path("2020-01","hf","rth","factors").with_suffix(".json")
        data=json.loads(source.read_text(encoding="utf-8"))
        data["modified"]=True
        source.write_text(json.dumps(data),encoding="utf-8")
        with patch.object(v,"_calendar",return_value=CAL):
            with self.assertRaisesRegex(ValueError,"factor input revision mismatch"):
                pre.run(r["dataset_id"])

    def test_canonical_bridge_cannot_bypass_unified_lineage(self):
        from atlaspm.research import data_admission as gate
        self.seed()
        with patch.object(v,"_calendar",return_value=CAL):
            with self.assertRaisesRegex(ValueError,"UNIFIED_FACTOR_LINEAGE_BLOCKED"):
                gate.build_research_dataset("2020-01","2020-02",features=["mom_5d"],horizons=[1])
        with self.assertRaisesRegex(ValueError,"cannot be overridden"):
            gate.build_research_dataset("2020-01","2020-02",verify_lineage=False)
