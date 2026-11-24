"""Offline orchestration tests. Mocked backends are not actual data evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m as hf, unified_pipeline as pipeline
from atlaspm.data.unified import AdmissionDenied, DataUnavailable
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
        build.assert_called_once_with("2020-01", source="hf", memory="256MB", threads=1)
        self.assertTrue(report["all_requested_months_processed"])
        self.assertFalse(report["approved_for_model_training"])

    def test_research_missing_data_and_gate(self):
        report = data_admission.inspect_real_factors("2020-01", "2020-01")
        self.assertFalse(report["structural_preflight_passed"])
        self.assertFalse(report["approved_for_backtest"])
        self.assertFalse(report["live_execution_enabled"])
        self.assertIn("MISSING_BAR_FACTOR_OR_LINEAGE_FILE", report["months"][0]["blockers"])
        with self.assertRaises(AdmissionDenied):
            data_admission.require_real_training_admission()
