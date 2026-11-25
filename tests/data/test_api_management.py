import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch
from atlaspm.data.api_gateway import Gateway
from atlaspm.monitoring import api_management as m
from atlaspm.data.sec_disclosures import recent_events


class APIManagementTests(unittest.TestCase):
    def test_missing_keys_and_explicit_disable_no_network(self):
        g=Gateway(opener=lambda *a,**kw:self.fail("network"))
        with patch.dict(os.environ,{"XPRESSAPI_TOKEN":"","ATLASPM_ENABLE_TREASURY":"0"}):
            rows={r["provider"]:r for r in m.inventory(g)}
            self.assertFalse(rows["xpressapi"]["ready_for_manual_attempt"])
            self.assertFalse(rows["treasury"]["enabled_by_configuration"])
            self.assertEqual(rows["bloomberg"]["mode"],"BLOCKED_WIRE_SPEC_OR_ENTITLEMENT")
            with self.assertRaises(ValueError):
                m.public_smoke(g)

    def test_health_reports_success_without_credentials(self):
        g=Gateway(opener=lambda *a,**kw:BytesIO(b'{"data":[{}]}'))
        with patch.dict(os.environ,{"XPRESSAPI_TOKEN":"private-fixture"}):
            self.assertEqual(m.public_smoke(g)["status"],"PUBLIC_RESPONSE_SCHEMA_CHECKED")
            rows={r["provider"]:r for r in m.inventory(g)}
            self.assertEqual(rows["treasury"]["success_rate"],1)
            self.assertIsNotNone(rows["treasury"]["last_latency_seconds"])
            self.assertNotIn("private-fixture",json.dumps(rows))

    def test_sec_recent_submissions_to_event(self):
        payload={"cik":"320193","filings":{"recent":{"accessionNumber":["0000320193-20-000001"],
            "form":["8-K"],"acceptanceDateTime":["2020-01-02T15:00:00Z"],"filingDate":["2020-01-02"]}}}
        g=Gateway(opener=lambda *a,**kw:BytesIO(json.dumps(payload).encode()))
        with patch.dict(os.environ,{"ATLASPM_SEC_USER_AGENT":"AtlasPM fixture@example.org"}):
            result=recent_events("320193",gateway=g)
        self.assertEqual(result["events"][0]["kind"],"sec_disclosure")
        self.assertIsNone(result["events"][0]["first_available_at"])
        self.assertIsNone(result["events"][0]["published_at"])
        self.assertEqual(result["filings"][0]["accepted_utc"],"2020-01-02T15:00:00+00:00")
        self.assertFalse(result["coverage_complete"])

    def test_fed_rss_uses_shared_transport_and_rejects_dtd(self):
        from atlaspm.data.fed_disclosures import recent_events as fed_events
        payload=b'<rss><channel><item><title>FOMC statement</title><link>https://www.federalreserve.gov/news.htm</link><pubDate>Thu, 02 Jan 2020 15:00:00 GMT</pubDate></item></channel></rss>'
        g=Gateway(opener=lambda *a,**kw:BytesIO(payload))
        out=fed_events(gateway=g)
        self.assertEqual(out["events"][0]["kind"],"rate_decision")
        self.assertIsNone(out["events"][0]["first_available_at"])
        bad=Gateway(opener=lambda *a,**kw:BytesIO(b'<!DOCTYPE rss><rss><channel/></rss>'))
        with self.assertRaises(ValueError):
            fed_events(gateway=bad)


    def test_local_enable_switch_is_not_credential_or_order_approval(self):
        g=Gateway(opener=lambda *a,**kw:self.fail("network"))
        with patch.dict(os.environ,{"XPRESSAPI_TOKEN":"","XPRESSAPI_USERNAME":"","XPRESSAPI_PASSWORD":""}):
            m.set_enabled("xpressapi",True)
            row=next(r for r in m.inventory(g) if r["provider"]=="xpressapi")
            self.assertEqual(row["effective_status"],"DISABLED_OR_BLOCKED")
            m.set_enabled("xpressapi",False)
            self.assertFalse(next(r for r in m.inventory(g) if r["provider"]=="xpressapi")["enabled_by_configuration"])
            with self.assertRaises(ValueError):
                m.set_enabled("live_orders",True)
