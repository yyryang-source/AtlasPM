"""Offline exact-symbol overlap triage, never identity certification."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from atlaspm.data import hf_stock5m_identity_market_review as r


def page():
    return {
        "report_path": "local_data/page.json", "index_partition_sha256": "a"*64,
        "page_complete": True, "page_advance_allowed": True,
        "resume_after_cik": "0000000002", "selected_issuers": ["0000000001", "0000000002"],
        "filings_with_bodies": 2,
        "results": [
            {"cik": "0000000001", "issuer": "Example", "filed_date": "2000-03-21",
             "source_url": "https://www.sec.gov/example", "sha256": "b"*64,
             "legacy_listing_text_leads": [{"symbol_candidate": "ABC", "exchange_reported": "NYSE"}],
             "legacy_symbol_change_text_leads": [{"old_symbol_candidate": "OLD",
                 "new_symbol_candidate": "ABC", "reported_change_date": "2000-03-20"}]},
            {"cik": "0000000002", "issuer": "Other", "filed_date": "2000-03-22",
             "source_url": "https://www.sec.gov/other", "sha256": "c"*64,
             "legacy_listing_text_leads": [{"symbol_candidate": "ABC"}]},
        ],
    }


class MarketReviewTests(unittest.TestCase):
    def test_overlap_cutoffs_collision_and_permissions(self):
        scan = ({"month": "2000-03", "status": "SCANNED"},
                [("ABC", "2000-03-20", 3), ("ABC", "2000-03-21", 4),
                 ("ABC", "2000-03-22", 5)])
        with patch.object(r.identity, "run", return_value=page()) as run, \
             patch.object(r, "_scan_month", return_value=scan), \
             patch.object(r.ev, "_json"), patch.object(r, "private_path", side_effect=lambda p: p):
            out = r.run(2000, "2000-03", "2000-03")
        self.assertFalse(run.call_args.kwargs["fetch"])
        self.assertEqual(out["unique_candidate_symbols"], 2)
        self.assertEqual(out["observed_candidate_symbols"], 1)
        a = out["candidates"][0]
        self.assertEqual(a["relative_to_filing_date"], {"before": 1, "on": 1, "after": 1})
        self.assertEqual(a["observed_full_slot_bars"], 12)
        self.assertTrue(a["symbol_claimed_by_multiple_sampled_issuers"])
        old = next(x for x in out["candidates"] if x["kind"] == "CHANGE_OLD")
        self.assertEqual(old["market_overlap_status"], "NOT_OBSERVED_IN_SCANNED_MONTHS")
        self.assertIsNone(old["security_id"])
        for key in ("approved_for_model_training", "historical_security_identity_verified",
                    "independent_source_verified", "approved_for_backtest", "live_execution_enabled"):
            self.assertFalse(out[key])
        self.assertEqual(out["claims_ingested"], 0)

    def test_missing_partition_is_not_negative_evidence(self):
        with patch.object(r.identity, "run", return_value=page()), \
             patch.object(r, "_scan_month", return_value=({"status": "MISSING_LOCAL_PARTITION"}, [])), \
             patch.object(r.ev, "_json"), patch.object(r, "private_path", side_effect=lambda p: p):
            out = r.run(2000, "2000-03", "2000-03")
        self.assertFalse(out["scan_complete"])
        self.assertTrue(all(a["market_overlap_status"] == "INSUFFICIENT_SCAN" for a in out["candidates"]))

    def test_scan_filters_exact_symbol_in_duckdb(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("lake extra required")
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/"source"
            path.write_bytes(b"original")
            def view(cx, month):
                cx.execute("""CREATE TEMP TABLE candidate_bars(symbol VARCHAR, trade_date_et DATE)""")
                cx.execute("""INSERT INTO candidate_bars VALUES
                    ('ABC','2000-03-21'),('ABC','2000-03-21'),('ABC.B','2000-03-21')""")
                return "candidate_bars"
            with patch.object(r.hf, "output_path", return_value=path), \
                 patch.object(r.hf, "_open_duckdb", side_effect=lambda: duckdb.connect(":memory:")), \
                 patch.object(r.v4, "install_research_view", side_effect=view), \
                 patch.object(r.v4, "calendar_digest", return_value="a"*64):
                report, rows = r._scan_month("2000-03", ["ABC"])
            self.assertEqual(rows, [("ABC", "2000-03-21", 2)])
            self.assertEqual(report["matched_symbol_days"], 1)
            self.assertEqual(path.read_bytes(), b"original")
