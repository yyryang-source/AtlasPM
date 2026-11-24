"""Offline synthetic tests for cached HF archive-wide QC; no market-data network."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m, hf_stock5m_audit, hf_stock5m_batch as batch


class HF5mBatchAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = Path.cwd()
        os.chdir(self.tmp.name)
        Path("local_data").mkdir()
        self.calls = []

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def seed(self, month: str) -> None:
        out = hf_stock5m.output_path(month)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"synthetic-not-real-parquet" * 10)
        _, quality = hf_stock5m.quality_paths(out)
        quality.parent.mkdir(parents=True, exist_ok=True)
        quality.write_text('{"excluded_ambiguous_5m_buckets": 0}', encoding="utf-8")

    def fake_audit(self, month, session="rth", save=True):
        self.calls.append(month)
        result = {
            "month": month, "quality_status": "REVIEW_REQUIRED",
            "structural_issues": [], "five_minute_bars": 100,
            "unique_symbols": 10, "nyse_expected_sessions": 20,
            "observed_trade_dates": 20, "missing_whole_trading_dates": [],
            "invalid_five_minute_bars": 0, "duplicate_symbol_timestamp_rows": 0,
            "out_of_nyse_session_bars": 0, "sparse_five_minute_buckets": 20,
            "total_5m_buckets_quarantined": 2, "invalid_raw_source_rows": 1,
            "review_reasons": ["RAW_SOURCE_OHLCV_ANOMALIES"],
        }
        if save:
            path = batch._audit_path(month, session)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result), encoding="utf-8")
        return result

    def test_dry_run_has_no_network_no_audit_writes(self):
        self.seed("2026-02")
        with patch.object(hf_stock5m_audit, "audit", side_effect=AssertionError("called")), \
             patch.object(hf_stock5m, "download", side_effect=AssertionError("network")):
            result = batch.run("2026-02", "2026-03", dry_run=True)
        self.assertEqual(2, result["months_selected"])
        self.assertEqual(1, result["parquet_files_present"])
        self.assertEqual(1, result["parquet_files_missing"])
        self.assertFalse(batch._summary_dir("rth").exists())

    def test_full_audit_uses_resume_and_invalidates_changed_source_and_qc(self):
        self.seed("2026-02")
        self.seed("2026-03")
        with patch.object(hf_stock5m_audit, "audit", side_effect=self.fake_audit):
            a = batch.run("2026-02", "2026-03")
            self.assertEqual(2, len(self.calls))
            self.assertEqual(0, a["resumed_months"])
            self.assertEqual(200, a["total_five_minute_bars"])
            self.assertEqual(4, a["total_quarantined_5m_buckets"])
            self.assertEqual(2, a["total_invalid_raw_source_rows"])
            self.assertTrue(a["all_months_structurally_audited"])
            self.assertFalse(a["approved_for_model_training"])
            self.assertFalse(a["data_rights_verified"])
            b = batch.run("2026-02", "2026-03")
            self.assertEqual(2, b["resumed_months"])
            self.assertEqual(2, len(self.calls))
            source = hf_stock5m.output_path("2026-02")
            with source.open("ab") as f:
                f.write(b"changed")
            c = batch.run("2026-02", "2026-03")
            self.assertEqual(1, c["resumed_months"])
            self.assertEqual(["2026-02"], self.calls[-1:])
            _, qc = hf_stock5m.quality_paths(hf_stock5m.output_path("2026-03"))
            qc.write_text('{"excluded_ambiguous_5m_buckets": 5}', encoding="utf-8")
            d = batch.run("2026-02", "2026-03")
            self.assertEqual(1, d["resumed_months"])
            self.assertEqual(["2026-03"], self.calls[-1:])

    def test_old_monthly_audit_schema_forces_rerun(self):
        # The NYSE partial-session calculation changed in v2, so an old
        # checkpoint must not silently masquerade as a current valid audit.
        self.seed("2026-03")
        with patch.object(hf_stock5m_audit, "audit", side_effect=self.fake_audit):
            batch.run("2026-03", "2026-03")
            checkpoint = batch._meta_path("2026-03", "rth")
            data = json.loads(checkpoint.read_text(encoding="utf-8"))
            data["monthly_audit_schema"] = "atlaspm_hf_5m_research_qc_v1"
            checkpoint.write_text(json.dumps(data), encoding="utf-8")
            rerun = batch.run("2026-03", "2026-03")
        self.assertEqual(2, len(self.calls))
        self.assertEqual(0, rerun["resumed_months"])

    def test_corrupt_cached_audit_forces_rescan(self):
        self.seed("2026-03")
        with patch.object(hf_stock5m_audit, "audit", side_effect=self.fake_audit):
            batch.run("2026-03", "2026-03")
            batch._audit_path("2026-03", "rth").write_text("tampered")
            b = batch.run("2026-03", "2026-03")
        self.assertEqual(2, len(self.calls))
        self.assertEqual(0, b["resumed_months"])

    def test_missing_and_error_not_promoted_to_success_or_cached(self):
        self.seed("2026-03")
        with patch.object(hf_stock5m_audit, "audit", side_effect=ValueError("corrupt parquet")):
            a = batch.run("2026-02", "2026-03")
            b = batch.run("2026-02", "2026-03")
        self.assertEqual(1, a["status_counts"][batch.MISSING])
        self.assertEqual(1, a["status_counts"][batch.ERROR])
        self.assertEqual(0, a["completed_months"])
        self.assertEqual(0, b["resumed_months"])
        self.assertFalse(a["all_months_structurally_audited"])
        self.assertEqual(["2026-02"], a["months_missing"])
        self.assertEqual(["2026-03"], a["months_audit_error"])

    def test_structural_failures_are_visible_and_not_approved(self):
        self.seed("2026-03")
        def structural(month, session="rth", save=True):
            result = self.fake_audit(month, session, save)
            result["quality_status"] = batch.STRUCTURAL
            result["structural_issues"] = ["INVALID_5M_BARS:1"]
            batch._audit_path(month, session).write_text(json.dumps(result))
            return result
        with patch.object(hf_stock5m_audit, "audit", side_effect=structural):
            result = batch.run("2026-03", "2026-03")
        self.assertEqual(1, result["status_counts"][batch.STRUCTURAL])
        self.assertEqual(["2026-03"], result["months_structural_failed"])
        self.assertFalse(result["all_months_structurally_audited"])

    def test_no_resume_and_max_months(self):
        self.seed("2026-02")
        self.seed("2026-03")
        with patch.object(hf_stock5m_audit, "audit", side_effect=self.fake_audit):
            batch.run("2026-02", "2026-03", max_months=1)
            result = batch.run("2026-02", "2026-03", max_months=1,
                               resume=False)
        self.assertEqual(1, result["months_selected"])
        self.assertEqual(2, len(self.calls))
        self.assertEqual(0, result["resumed_months"])

    def test_invalid_month_rejected_without_writing(self):
        with self.assertRaises(ValueError):
            batch.run("2026-13", "2026-03")
        self.assertFalse(batch._summary_dir("rth").exists())


if __name__ == "__main__":
    unittest.main()
