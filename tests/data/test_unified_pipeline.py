"""Offline orchestration tests. Mocked backends are not actual data evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m as hf, unified_pipeline as pipeline
from atlaspm.data.unified import DataUnavailable
from atlaspm.research import data_admission


class UnifiedPipelineTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cwd = Path.cwd()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir, cwd)

    def test_plan_and_missing_input(self):
        with patch.object(hf, "download", side_effect=AssertionError("network")):
            plan = pipeline.maintain("2020-01", "2020-02")
            self.assertEqual(plan["mode"], "plan")
            self.assertFalse(Path("local_data").exists())
            report = pipeline.maintain("2020-01", "2020-02", execute=True)
        self.assertEqual(report["months"][0]["status"], "BLOCKED")
        self.assertEqual(report["remaining_months"], 1)
        self.assertFalse(report["all_requested_months_processed"])
        with self.assertRaises(DataUnavailable):
            pipeline.maintain("2026-04", "2026-04")

    def test_existing_bars_not_replaced_and_failed_audit_blocks_factor(self):
        bars = hf.output_path("2020-01")
        bars.parent.mkdir(parents=True)
        bars.write_bytes(b"fixture")
        with (patch.object(hf, "convert", side_effect=AssertionError("overwrite")),
              patch.object(pipeline.qc, "run", return_value={
                  "all_months_structurally_audited": False, "status_counts": {"ERROR": 1}}),
              patch.object(pipeline.factor_db, "build") as build):
            report = pipeline.maintain("2020-01", "2020-01", execute=True)
        build.assert_not_called()
        self.assertEqual(report["months"][0]["status"], "BLOCKED")
        self.assertEqual(bars.read_bytes(), b"fixture")

    def test_existing_builder_cache_and_resume_reused(self):
        bars = hf.output_path("2020-01")
        bars.parent.mkdir(parents=True)
        bars.write_bytes(b"fixture")
        with (patch.object(pipeline.qc, "run", return_value={
                  "all_months_structurally_audited": True, "status_counts": {"REVIEW_REQUIRED": 1}}) as audit,
              patch.object(pipeline.factor_db, "build", return_value={"state": "existing"}) as build):
            report = pipeline.maintain("2020-01", "2020-01", execute=True)
        audit.assert_called_once_with("2020-01", "2020-01", session="rth", resume=True, dry_run=False)
        build.assert_called_once_with("2020-01", source="hf", memory="256MB", threads=1,strict_calendar=True)
        self.assertTrue(report["all_requested_months_processed"])
        self.assertFalse(report["approved_for_model_training"])

    def test_research_missing_data_and_gate(self):
        report = data_admission.inspect_real_factors("2020-01", "2020-01")
        self.assertFalse(report["structural_preflight_passed"])
        self.assertFalse(report["approved_for_backtest"])
        self.assertFalse(report["live_execution_enabled"])
        self.assertIn("MISSING_BAR_FACTOR_OR_LINEAGE_FILE", report["months"][0]["blockers"])
        with self.assertRaisesRegex(ValueError, "REAL_TRAINING_DATA_NOT_APPROVED"):
            data_admission.require_real_training_admission()

    def test_research_trainer_cannot_accept_self_declared_approval(self):
        from atlaspm.research.training_dataset import validate_panel_metadata
        for payload in ({"mode": "historical", "synthetic_fixture": False},
                        {"mode": "synthetic", "synthetic_fixture": False,
                         "approved_for_model_training": True}):
            with self.assertRaisesRegex(ValueError, "REAL_TRAINING_DATA_NOT_APPROVED"):
                validate_panel_metadata(payload, 1, 1)

    def test_actual_factor_preflight_content_revision(self):
        from importlib.util import find_spec
        if not find_spec("duckdb"):
            self.skipTest("lake dependency")
        import duckdb
        from contextlib import closing
        bars = hf.output_path("2020-01")
        bars.parent.mkdir(parents=True)
        quoted = str(bars).replace("\\", "/").replace("'", "''")
        with closing(duckdb.connect()) as con:
            con.execute(f"""COPY (SELECT 'A' AS symbol,
                DATE '2020-01-02' AS trade_date_et,
                1577975400000::BIGINT AS timestamp_ms,
                10.0::DOUBLE AS open, 11.0::DOUBLE AS high,
                9.0::DOUBLE AS low, 10.0::DOUBLE AS close,
                100::DOUBLE AS volume_shares, 5::BIGINT AS source_minute_bars,
                5::BIGINT AS unique_source_minutes)
                TO '{quoted}' (FORMAT PARQUET)""")
        pipeline.factor_db.build("2020-01", memory="256MB", threads=1)
        result = data_admission.inspect_real_factors("2020-01", "2020-01")
        self.assertTrue(result["structural_preflight_passed"], result)
        self.assertFalse(result["approved_for_model_training"])
        self.assertEqual(result["months"][0]["factor_rows"], 1)
        # Exercise the actual benchmark process on this fixture. These counters
        # measure a synthetic test only, never production archive performance.
        import subprocess
        import sys
        import json
        script = Path(__file__).resolve().parents[2] / "scripts/maintenance/benchmark_unified_data.py"
        proc = subprocess.run([sys.executable, str(script), "--dataset", "equity_5m",
                               "--start", "2020-01-02", "--end", "2020-01-02",
                               "--column", "close", "--no-tracemalloc"],
                              capture_output=True, text=True, check=True)
        measured = json.loads(proc.stdout)
        self.assertEqual(measured["rows"], 1)
        self.assertEqual(measured["emitted_source_file_bytes"], bars.stat().st_size)
        self.assertIsNone(measured["python_peak_bytes"])
        self.assertFalse(measured["approved_for_model_training"])
        self.assertEqual(result["evidence_id"],
                         data_admission.inspect_real_factors("2020-01", "2020-01")["evidence_id"])
        if find_spec("pandas_market_calendars"):
            dataset = data_admission.build_research_dataset(
                "2020-01", "2020-01", features=["mom_5d"], horizons=[1],
                minimum_train_sessions=1, test_sessions=1, embargo_sessions=0)
            self.assertEqual(dataset["lineage_id"], result["evidence_id"])
            self.assertEqual(dataset["rows"], 1)
            preflight = data_admission.inspect_research_dataset(dataset["dataset_id"])
            self.assertTrue(preflight["feature_source_reconciled"])
            self.assertFalse(preflight["approved_for_model_training"])
        # Change an actual recorded source (even if content is unchanged);
        # the existing builder's lineage signature must now be stale.
        old = bars.stat()
        os.utime(bars, ns=(old.st_atime_ns, old.st_mtime_ns + 2_000_000_000))
        changed = data_admission.inspect_real_factors("2020-01", "2020-01")
        self.assertFalse(changed["structural_preflight_passed"])
        self.assertIn("STALE_DAILY_INPUT", changed["months"][0]["blockers"])
        self.assertNotEqual(result["evidence_id"], changed["evidence_id"])


    def test_early_close_projection_actual_daily_and_raw_preservation(self):
        from importlib.util import find_spec
        if not find_spec("duckdb"):
            self.skipTest("lake dependency")
        import duckdb
        from contextlib import closing
        from datetime import date, datetime
        from zoneinfo import ZoneInfo
        from atlaspm.data import hf_stock5m_session_v4 as v4
        month = "2000-07"
        day = date(2000,7,3)
        def stamp(hour,minute):
            return int(datetime(2000,7,3,hour,minute,tzinfo=ZoneInfo("America/New_York")).timestamp()*1000)
        bars=hf.output_path(month)
        bars.parent.mkdir(parents=True)
        with closing(duckdb.connect()) as con:
            con.execute("""CREATE TABLE bars(symbol VARCHAR,trade_date_et DATE,timestamp_ms BIGINT,
                         open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume_shares DOUBLE,
                         source_minute_bars BIGINT,unique_source_minutes BIGINT)""")
            con.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?)",[
                ("A",day,stamp(12,55),10,10,10,10,100,1,1),
                ("A",day,stamp(13,0),99,99,99,99,900,1,1)])
            con.execute(f"COPY bars TO '{hf._quote(bars)}' (FORMAT PARQUET)")
        before=bars.read_bytes()
        calendar=[(day,stamp(9,30),stamp(13,0))]
        prior={"schema":v4.qc.SCHEMA,"five_minute_bars":2,"out_of_nyse_session_bars":1,
               "quality_status":"STRUCTURAL_QC_FAILED","structural_issues":["OUTSIDE_NYSE_SESSIONS:1"],
               "missing_whole_trading_dates":[]}
        audit={"all_months_structurally_audited":False,
               "status_counts":{"STRUCTURAL_QC_FAILED":1},"months_structural_failed":[month]}
        with (patch.object(v4.qc,"_calendar",return_value=calendar),
              patch.object(v4.qc,"audit",return_value=prior),
              patch.object(pipeline.qc,"run",return_value=audit)):
            # Unrelated failures must remain blocked before writing daily data.
            with patch.object(v4.qc,"audit",return_value={**prior,
                       "structural_issues":["OUTSIDE_NYSE_SESSIONS:1","INVALID_5M_BARS:1"]}):
                rejected=pipeline.maintain(month,month,execute=True)
            self.assertFalse(rejected["all_requested_months_processed"])
            daily=pipeline.factor_db._path(month,"hf","rth","daily")
            self.assertFalse(daily.exists())
            result=pipeline.maintain(month,month,execute=True)
            self.assertTrue(result["all_requested_months_processed"],result)
            self.assertEqual(result["months"][0]["session_projection"]["excluded_bars"],1)
            self.assertEqual(result["months"][0]["status"],"EXPLORATORY_NYSE_PROJECTION_REVIEW_REQUIRED")
            with closing(duckdb.connect()) as con:
                values=con.execute(f"SELECT close,volume_shares,observed_5m_bars FROM read_parquet('{hf._quote(daily)}')").fetchone()
            self.assertEqual(values,(10,100,1))
            self.assertEqual(bars.read_bytes(),before)
            self.assertFalse(result["approved_for_model_training"])
            self.assertTrue(pipeline.factor_preflight(month,month)["structural_preflight_passed"])
            repeat=pipeline.maintain(month,month,execute=True)
            self.assertEqual(repeat["months"][0]["factors"],"existing")
            self.assertFalse(repeat["approved_for_model_training"])
            with patch.object(v4.qc,"_calendar",return_value=[(day,stamp(9,30),stamp(13,5))]):
                changed=pipeline.factor_preflight(month,month)
            self.assertFalse(changed["structural_preflight_passed"])
