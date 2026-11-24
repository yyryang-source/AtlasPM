"""Licensed-export fixtures only; never scrape or access a news provider."""
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.research import news_intelligence as news
from atlaspm.research.intelligence import _prepare


class NewsIntelligenceTests(unittest.TestCase):
    def setUp(self):
        cwd = Path.cwd()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        os.chdir(temp.name)
        self.addCleanup(os.chdir, cwd)
        Path("local_data").mkdir()
        self.path = Path("local_data/feed.json")
        self.row = {"id": "story-1", "title": "Federal Reserve announces interest rate decision",
                    "source": "Fixture publisher", "published_at": (datetime.now(UTC)-timedelta(minutes=5)).isoformat(),
                    "url": "https://example.org/story?tracking=secret", "symbols": ["SPY"], "sectors": ["Financials"]}
        self.env = {"ATLASPM_YAHOO_NEWS_RIGHTS": "local-test",
                    "ATLASPM_YAHOO_NEWS_RIGHTS_RETENTION": "1"}
        self.path.write_text(json.dumps([self.row]), encoding="utf-8")

    def test_authorization_required_and_no_writes(self):
        with patch.dict(os.environ, {"ATLASPM_YAHOO_NEWS_RIGHTS": ""}):
            with self.assertRaisesRegex(ValueError, "RIGHTS"):
                news.ingest("yahoo_finance", self.path)
        self.assertFalse(Path("local_data/news").exists())

    def test_incremental_dedup_revisions_and_local_cutoff(self):
        before = datetime.now(UTC).isoformat()
        with patch.dict(os.environ, self.env):
            a = news.ingest("yahoo_finance", self.path)
            b = news.ingest("yahoo_finance", self.path)
            old = news.review("yahoo_finance", a["month"], as_of=before)
            self.assertEqual(old["events"], [])
            self.assertEqual(a["added"], 1)
            self.assertEqual(b["added"], 0)
            self.row["title"] = "Federal Reserve cuts interest rate"
            self.path.write_text(json.dumps([self.row]), encoding="utf-8")
            c = news.ingest("yahoo_finance", self.path)
            report = news.review("yahoo_finance", a["month"], as_of=datetime.now(UTC).isoformat())
        self.assertEqual(c["added"], 1)
        self.assertEqual(len(report["events"]), 1)
        e = report["events"][0]
        self.assertEqual(e["title"], self.row["title"])
        self.assertEqual(e["event_categories"], ["monetary_policy"])
        self.assertEqual(e["importance_heuristic"], 5)
        self.assertEqual(e["url"], "https://example.org/story")
        self.assertIsNone(e["sector_impact_direction"])
        self.assertFalse(report["external_ai_allowed"])

    def test_future_or_untrusted_url_rejected(self):
        with patch.dict(os.environ, self.env):
            for updates in ({"published_at": (datetime.now(UTC)+timedelta(days=1)).isoformat()},
                            {"url": "https://user:secret@example.org/story"}):
                self.path.write_text(json.dumps([{**self.row, **updates}]), encoding="utf-8")
                with self.assertRaises(ValueError):
                    news.ingest("yahoo_finance", self.path)
        self.assertFalse(Path("local_data/news").exists())

    def test_no_external_egress_from_designated_sources(self):
        for source in ("Yahoo Finance", "Bloomberg", "Unusual Whales", "IBKR", "Interactive Brokers"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "Restricted feed"):
                    _prepare("news", [{"evidence_id":"a", "source":source,
                                      "available_at":"2020-01-01T00:00:00Z",
                                      "text":"fixture", "external_use_reviewed":True}],
                             "2020-01-02T00:00:00Z")
