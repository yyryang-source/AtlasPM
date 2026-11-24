from datetime import UTC, datetime, timedelta
import os
import unittest
from unittest.mock import patch
from atlaspm.research import financial_events as e


class EventTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(UTC)
        self.row={"provider":"msci","source_id":"event1","kind":"index_change",
                  "subjects":["ticker:ABC"],"published_at":(self.now-timedelta(minutes=1)).isoformat(),
                  "first_available_at":None,"effective_at":(self.now+timedelta(days=1)).isoformat(),
                  "evidence_sha256":"a"*64,"delivery":"file","expiry_seconds":3600}
        env=patch.dict(os.environ,{"ATLASPM_MSCI_RIGHTS":"fixture"})
        env.start()
        self.addCleanup(env.stop)

    def test_future_effective_not_future_publication_and_duplicate(self):
        item=e.normalize(self.row,received_at=self.now.isoformat())
        out=e.review([item,item],as_of=self.now.isoformat(),holdings=["ticker:ABC"])
        self.assertEqual(len(out["events"]),1)
        self.assertEqual(out["excluded"]["duplicate"],1)
        self.assertTrue(out["events"][0]["effective_pending"])
        self.assertTrue(out["events"][0]["risk_review_requested"])
        self.assertFalse(out["events"][0]["realtime_verified"])
        self.assertFalse(out["broker_order_submission_enabled"])

    def test_category_and_future_cutoff_fail_closed(self):
        with self.assertRaises(ValueError):
            e.normalize({**self.row,"kind":"credit_rating"},received_at=self.now.isoformat())
        item=e.normalize(self.row,received_at=self.now.isoformat())
        out=e.review([item],as_of=(self.now-timedelta(seconds=1)).isoformat())
        self.assertEqual(out["events"],[])
        with self.assertRaisesRegex(ValueError,"revision mismatch"):
            e.review([{**item,"subjects":["ticker:XYZ"]}],as_of=self.now.isoformat())

    def test_delayed_event_expiry(self):
        row={**self.row,"delivery":"delayed","expiry_seconds":1}
        item=e.normalize(row,received_at=self.now.isoformat())
        out=e.review([item],as_of=self.now.isoformat())
        self.assertTrue(out["events"][0]["expired"])
        self.assertEqual(out["research_requests"],0)
