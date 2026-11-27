import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from atlaspm.data.sec_latest_filings import recent_filings
from atlaspm.data.api_gateway import Gateway, GatewayError
from atlaspm.research.disclosure_monitor import poll_once


class DisclosureMonitorTests(unittest.TestCase):
    def fixture(self, host="www.sec.gov"):
        stamp = "2024-01-01T12:00:00+00:00"
        url = f"https://{host}/Archives/edgar/data/320193/000032019324000001/0000320193-24-000001-index.htm"
        body = f"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <title>8-K - Example</title><updated>{stamp}</updated>
        <link rel="alternate" href="{url}"/></entry></feed>""".encode()
        return SimpleNamespace(fetch=lambda *args: SimpleNamespace(
            body=body, fetched_at=datetime.now(UTC).isoformat(),
            evidence=lambda: {"provider": "sec_feed"}))

    def test_normalized_event(self):
        result = recent_filings(gateway=self.fixture())
        event = result["events"][0]
        self.assertEqual(event["source_id"], "0000320193-24-000001")
        self.assertEqual(event["subjects"], [])
        self.assertIsNone(event["published_at"])
        self.assertFalse(result["coverage_complete"])

    def test_external_link_blocked(self):
        with self.assertRaises(GatewayError):
            recent_filings(gateway=self.fixture("example.com"))

    def test_gateway_scope(self):
        with self.assertRaises(GatewayError):
            Gateway._request("sec_feed", "https://www.sec.gov/other", None)

    def test_pause_prevents_fetch(self):
        with patch("atlaspm.research.disclosure_monitor.event_queue._read_state",
                   return_value={"__system_control__": {"stopped": True}}):
            self.assertEqual(poll_once("sec", fetcher=lambda: self.fail("network")),
                             {"source": "sec", "status": "PAUSED"})

    def test_queue_handoff(self):
        report = recent_filings(gateway=self.fixture())
        with patch("atlaspm.research.disclosure_monitor.event_queue._read_state", return_value={}), \
             patch("atlaspm.research.disclosure_monitor.event_queue.enqueue",
                   return_value={"added": 1, "retained": 1}) as enqueue:
            result = poll_once("sec", fetcher=lambda: report)
        self.assertEqual(result["added"], 1)
        self.assertEqual(enqueue.call_args.args[0], report["events"])
