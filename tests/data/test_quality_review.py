"""Offline checks for monthly evidence review; no fixture confers admission."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atlaspm.data import quality_review as q


class QualityReviewTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cwd=Path.cwd()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir,cwd)

    def test_missing_factors_block_without_creating_database(self):
        report=q.review_month("2020-01")
        self.assertIn("FACTOR_LINEAGE_BLOCKED",report["blockers"])
        self.assertFalse(report["approved_for_model_training"])
        self.assertFalse(Path("local_data").exists())

    def test_missing_ledger_never_grants_training(self):
        with patch.object(q.unified_pipeline,"factor_preflight",return_value={
            "evidence_id":"fixture","structural_preflight_passed":True,"blockers":[]}):
            report=q.review_month("2020-01")
        self.assertIn("IDENTITY_ACTION_LEDGER_UNAVAILABLE_OR_CORRUPT",report["blockers"])
        self.assertFalse(report["live_execution_enabled"])
        self.assertFalse(Path("local_data").exists())

    def test_logical_ledger_hash_tracks_revision(self):
        from contextlib import closing
        with closing(q.ledger._db()) as con:
            first=q._ledger_digest()
            self.assertEqual(first,q._ledger_digest())
            with con:
                con.execute("INSERT INTO claims VALUES ('bad','IDENTITY','x','A','2020-01-01','{}')")
        with self.assertRaisesRegex(ValueError,"ledger integrity failure"):
            q._ledger_digest()
