"""Offline V4 calendar projection tests."""
from datetime import date, datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m as hf, hf_stock5m_audit as qc
from atlaspm.data import hf_stock5m_session_v4 as v4

try:
    import duckdb
    import pandas_market_calendars
except ImportError:
    duckdb = None


def ms(value):
    return int(datetime.fromisoformat(value + "+00:00").timestamp() * 1000)


@unittest.skipUnless(duckdb is not None, "Install lake/calendar extras")
class SessionV4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prev = Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir, self.prev)
        Path("local_data").mkdir()
        self.path = hf.output_path("2025-11")
        self.path.parent.mkdir(parents=True)
        cx = duckdb.connect()
        try:
            cx.execute("CREATE TABLE bars(symbol VARCHAR, trade_date_et DATE, timestamp_ms BIGINT)")
            cx.executemany("INSERT INTO bars VALUES (?,?,?)", [
                ("AAA", "2025-11-28", ms("2025-11-28T14:25:00")),
                ("AAA", "2025-11-28", ms("2025-11-28T14:30:00")),
                ("AAA", "2025-11-28", ms("2025-11-28T17:55:00")),
                ("AAA", "2025-11-28", ms("2025-11-28T18:00:00")),
                ("AAA", "2025-11-27", ms("2025-11-27T14:30:00")),
                ("AAA", "2025-11-26", ms("2025-11-26T21:00:00")),
            ])
            cx.execute(f"COPY bars TO '{hf._quote(self.path)}' (FORMAT PARQUET)")
        finally:
            cx.close()
        self.prior = {"schema": qc.SCHEMA, "month": "2025-11", "session": "rth",
                      "quality_status": "STRUCTURAL_QC_FAILED", "five_minute_bars": 6,
                      "out_of_nyse_session_bars": 4,
                      "structural_issues": ["OUTSIDE_NYSE_SESSIONS:4"]}

    def test_real_early_close_and_non_destructive_projection(self):
        before = self.path.read_bytes()
        result = v4.diagnose("2025-11", self.prior)
        self.assertEqual(result["status"], "SESSION_DIAGNOSTIC_COMPLETE")
        self.assertEqual(result["outside_nyse_bars"], 4)
        self.assertEqual(result["counts"], {
            "AFTER_EARLY_CLOSE": 1, "AFTER_REGULAR_CLOSE": 1,
            "BEFORE_OPEN": 1, "FULL_NYSE_SLOT": 2, "NON_NYSE_DATE": 1,
        })
        cx = duckdb.connect()
        try:
            v4.install_research_view(cx, "2025-11")
            observed = cx.execute("SELECT timestamp_ms,bar_available_at_ms FROM v4_research_bars ORDER BY 1").fetchall()
            self.assertEqual(len(observed), 2)
            self.assertTrue(all(end - start == 300000 for start, end in observed))
        finally:
            cx.close()
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(result["approved_for_model_training"])
        self.assertFalse(result["live_execution_enabled"])

    def test_partial_boundary_excluded_not_clipped(self):
        with patch.object(qc, "_calendar", return_value=[
            (date(2025, 11, 28), ms("2025-11-28T14:32:00"), ms("2025-11-28T17:58:00"))
        ]):
            result = v4.diagnose("2025-11")
            self.assertEqual(result["counts"]["PARTIAL_BOUNDARY"], 2)
            self.assertEqual(result["research_candidate_bars"], 0)
            self.assertEqual(result["status"], "PRIOR_AUDIT_RECONCILIATION_REQUIRED")

    def test_other_errors_preserved_and_count_mismatch(self):
        prior = dict(self.prior, structural_issues=["INVALID_5M_BARS:1"])
        self.assertEqual(v4.diagnose("2025-11", prior)["status"], "STRUCTURAL_REVIEW_REQUIRED")
        prior = dict(self.prior, five_minute_bars=7)
        self.assertEqual(v4.diagnose("2025-11", prior)["status"], "PRIOR_AUDIT_RECONCILIATION_REQUIRED")

    def test_batch_plan_and_report(self):
        dest = Path("local_data/stock_5m/huggingface/quality/audits/session=rth/2025-11.json")
        dest.parent.mkdir(parents=True)
        dest.write_text(json.dumps(self.prior), encoding="utf-8")
        plan = v4.run("2025-11", "2025-11", dry_run=True)
        self.assertEqual(plan["target_months"], ["2025-11"])
        self.assertEqual(plan["months_scanned"], 0)
        self.assertNotIn("summary_path", plan)
        result = v4.run("2025-11", "2025-11")
        self.assertEqual(result["outside_nyse_bars"], 4)
        self.assertTrue(Path(result["summary_path"]).is_file())
        self.assertEqual(json.loads(dest.read_text()), self.prior)
        self.assertFalse(result["approved_for_model_training"])

    def test_dst_calendar(self):
        rows = {d.isoformat(): opening for d, opening, _, _ in v4.calendar_rows("2025-03")}
        self.assertEqual(rows["2025-03-07"], ms("2025-03-07T14:30:00"))
        self.assertEqual(rows["2025-03-10"], ms("2025-03-10T13:30:00"))
