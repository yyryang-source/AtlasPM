"""Independent-reference triage tests; no real claim ingestion."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atlaspm.data import hf_stock5m_identity_reference_review as r
from atlaspm.data import hf_stock5m_identity_market_review as market


def candidate(**overrides):
    return dict({"cik": "0000000001", "issuer": "Example", "symbol": "ABC",
                 "kind": "LISTING_TEXT", "filed_date": "2000-03-21",
                 "reported_change_date": None, "reported_exchange": "NYSE",
                 "source_sha256": "b"*64, "source_url": "https://www.sec.gov/example",
                 "source_lead": {}}, **overrides)


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        old = Path.cwd()
        os.chdir(tmp.name)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir, old)
        Path("local_data").mkdir()
        self.raw = Path("local_data/raw.txt")
        self.raw.write_bytes(b"synthetic original external record")
        self.file = Path("local_data/references.jsonl")

    def row(self, **overrides):
        return dict({"reference_id": "r1", "source_family": "vendor",
                     "source_url": "https://example.org/archive", "reviewer": "reviewer",
                     "evidence_path": str(self.raw), "evidence_sha256": r.ev._sha(self.raw.read_bytes()),
                     "known_date": "2000-03-20", "cik": "0000000001", "symbol": "ABC",
                     "source_security_key": "vendor-123", "exchange": "XNYS",
                     "valid_from": "2000-03-01", "valid_to": "2000-04-01"}, **overrides)

    def write(self, *rows):
        self.file.write_text("".join(json.dumps(x)+"\n" for x in rows), encoding="utf-8")
        return self.file

    def review(self, candidates=None, **kw):
        return r.review(candidates or [candidate()], {"ABC": {
            "2000-03-20": 2, "2000-03-21": 3, "2000-03-22": 4, "2000-04-01": 5}},
            as_of="2000-05-01", scan_complete=True, **kw)

    def test_queue_without_reference_and_permission_guards(self):
        out = self.review()
        self.assertEqual(out["status"], "EXTERNAL_REFERENCE_REQUIRED")
        self.assertEqual(len(out["evidence_requests"]), 1)
        self.assertEqual(out["daily_status_counts"]["SEC_NOT_PRIOR_KNOWN"], 2)
        self.assertEqual(out["prior_known_declared_overlap_symbol_days"], 0)
        self.assertEqual(out["claims_ingested"], 0)
        for k in ("approved_for_model_training", "approved_for_backtest",
                  "independent_source_verified", "coverage_verified",
                  "historical_security_identity_verified", "live_execution_enabled"):
            self.assertFalse(out[k])

    def test_hash_bound_reference_and_exclusive_end(self):
        out = self.review(reference_file=self.write(self.row()))
        self.assertEqual(out["point_status_counts"], {"DECLARED_CIK_SYMBOL_MIC_AGREEMENT": 1})
        self.assertEqual(out["prior_known_declared_overlap_symbol_days"], 1)
        self.assertEqual(out["daily_status_counts"]["NO_VISIBLE_REFERENCE_INTERVAL"], 1)
        self.assertEqual(out["observed_listing_candidate_symbol_days"], 4)
        self.assertIsNone(out["point_reviews"][0]["security_id"])
        self.assertEqual(out["certified_identities"], 0)
        self.assertFalse(out["independent_source_verified"])

    def test_later_reference_is_retrospective_only(self):
        out = self.review(reference_file=self.write(self.row(known_date="2000-04-02")))
        self.assertEqual(out["point_status_counts"], {"DECLARED_CIK_SYMBOL_MIC_AGREEMENT": 1})
        self.assertEqual(out["prior_known_declared_overlap_symbol_days"], 0)
        out = self.review(reference_file=self.write(self.row(known_date="2000-03-22")))
        self.assertEqual(out["prior_known_declared_overlap_symbol_days"], 0)
        out = self.review(reference_file=self.write(self.row(known_date="2000-05-01")))
        self.assertEqual(out["point_status_counts"], {"NO_VISIBLE_REFERENCE_INTERVAL": 1})

    def test_reference_conflict_and_same_family_security_keys(self):
        a = self.row()
        for b, expected in (
            (self.row(reference_id="r2", cik="0000000002"), "REFERENCE_CIK_CONFLICT"),
            (self.row(reference_id="r2", source_security_key="other"), "REFERENCE_SECURITY_KEY_CONFLICT"),
            (self.row(reference_id="r2", exchange="XNAS"), "REFERENCE_EXCHANGE_CONFLICT"),
        ):
            with self.subTest(expected=expected):
                out = self.review(reference_file=self.write(a,b))
                self.assertEqual(out["point_status_counts"], {expected: 1})
                self.assertEqual(out["prior_known_declared_overlap_symbol_days"], 0)

    def test_cross_symbol_source_key_overlap_and_cutoff(self):
        a = self.row()
        b = self.row(reference_id="r2", symbol="OTHER")
        out = self.review(reference_file=self.write(a, b))
        self.assertEqual(out["point_status_counts"], {"REFERENCE_SOURCE_KEY_OVERLAP": 1})
        self.assertEqual(out["prior_known_declared_overlap_symbol_days"], 0)
        b["known_date"] = "2000-04-02"
        out = self.review(reference_file=self.write(a, b))
        self.assertEqual(out["point_status_counts"], {"REFERENCE_SOURCE_KEY_OVERLAP": 1})
        self.assertEqual(out["prior_known_declared_overlap_symbol_days"], 1)
        b["valid_from"] = "2000-04-01"
        b["valid_to"] = None
        out = self.review(reference_file=self.write(a, b))
        self.assertEqual(out["point_status_counts"], {"DECLARED_CIK_SYMBOL_MIC_AGREEMENT": 1})

    def test_cik_reuse_no_day_double_count_and_future_sec(self):
        f = self.write(self.row())
        out = self.review([candidate(), candidate()], reference_file=f)
        self.assertEqual(out["prior_known_declared_overlap_symbol_days"], 1)
        out = self.review([candidate(), candidate(cik="0000000002")], reference_file=f)
        self.assertEqual(out["point_status_counts"], {"SEC_CIK_COLLISION_REVIEW_REQUIRED": 2})
        self.assertEqual(out["prior_known_declared_overlap_symbol_days"], 0)
        out = self.review([candidate(), candidate(cik="0000000002", filed_date="2000-05-02")], reference_file=f)
        self.assertEqual(out["prior_known_declared_overlap_symbol_days"], 1)
        self.assertEqual(out["point_status_counts"]["SEC_LEAD_NOT_VISIBLE_AT_REVIEW_CUTOFF"], 1)

    def test_change_is_point_only_and_combined_venue_unresolved(self):
        f = self.write(self.row())
        c = candidate(kind="CHANGE_OLD", reported_change_date="2000-03-22")
        out = self.review([c], reference_file=f)
        self.assertEqual(out["point_reviews"][0]["anchor_calendar_date"], "2000-03-21")
        self.assertEqual(out["observed_listing_candidate_symbol_days"], 0)
        out = self.review([candidate(reported_exchange="New York and Pacific Stock Exchanges")], reference_file=f)
        self.assertEqual(out["point_status_counts"], {"SEC_EXCHANGE_UNRESOLVED": 1})

    def test_reject_bad_hash_sec_sources_duplicate_and_pipeline_artifact(self):
        rows = [self.row(evidence_sha256="0"*64),
                self.row(source_url="https://www.sec.gov/a"),
                self.row(source_url="https://www.sec.gov./a"),
                self.row(source_url="https://user@example.org/a"),
                self.row(valid_to="2000-03-01"),
                self.row(cik="1")]
        for row in rows:
            with self.assertRaises(ValueError):
                r.load_reference(self.write(row))
        with self.assertRaisesRegex(ValueError, "Duplicate reference ID"):
            r.load_reference(self.write(self.row(), self.row()))
        p = r.ROOT / "generated.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.raw.read_bytes())
        with self.assertRaisesRegex(ValueError, "not pipeline artifacts"):
            r.load_reference(self.write(self.row(evidence_path=str(p))))

    def test_market_integration_no_network_and_explicit_cutoff(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            market.run(2000, "2000-03", "2000-03", reference_file=self.file)
        page = {"results": [], "report_path": "local_data/page.json",
                "index_partition_sha256": "a"*64, "page_complete": True,
                "page_advance_allowed": True, "resume_after_cik": None,
                "selected_issuers": [], "filings_with_bodies": 0}
        with patch.object(market.identity, "run", return_value=page) as run:
            out = market.run(2000, "2000-03", "2000-03", reference_as_of="2000-05-01")
        self.assertFalse(run.call_args.kwargs["fetch"])
        self.assertEqual(out["reference_review"]["status"], "EXTERNAL_REFERENCE_REQUIRED")
        self.assertTrue(Path(out["report_path"]).is_file())
        self.assertFalse((r.ledger.BASE/"ledger.sqlite3").exists())
