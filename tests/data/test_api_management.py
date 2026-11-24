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
        self.assertFalse(result["coverage_complete"])
