"""Offline gateway tests, with no credentials or external calls."""
import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from atlaspm.data import api_gateway as api, market_fetch, sec_download

T = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/test"
M = "https://api.massive.com/v3/reference/options/contracts"
S = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.requests = []
        self.events = []
        self.gateway = api.Gateway(opener=self.open, clock=self.clock, sleep=self.clock.sleep)

    def open(self, request, timeout):
        self.requests.append((request, timeout, self.clock()))
        if self.events:
            event = self.events.pop(0)
            if isinstance(event, Exception):
                raise event
            return BytesIO(event)
        return BytesIO(b'{"data":[]}')

    def test_public_cache_dedup_and_receipt(self):
        first = self.gateway.fetch("treasury", T, cache_ttl=30)
        second = self.gateway.fetch("treasury", T, cache_ttl=30)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(second.cached)
        self.assertEqual(first.evidence()["sha256"], second.evidence()["sha256"])
        self.assertFalse(first.evidence()["approved_for_model_training"])
        self.assertFalse(first.evidence()["point_in_time_verified"])
        self.clock.sleep(31)
        self.gateway.fetch("treasury", T, cache_ttl=30)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.gateway.health()["treasury"]["cache_hits"], 1)

    def test_host_key_and_redirect_rejection(self):
        for url in (T.replace("https", "http"), T.replace("gov", "gov.evil.example"),
                    T+"?api_key=secret", T+"#fragment",
                    T.replace("api.", "api:secret@api.")):
            with self.subTest(url=url):
                with self.assertRaises(api.GatewayError):
                    self.gateway.fetch("treasury", url)
        self.assertFalse(self.requests)
        self.events = [HTTPError(T, 302, "redirect", {}, BytesIO(b""))]
        with self.assertRaisesRegex(api.GatewayError, "HTTP 302"):
            self.gateway.fetch("treasury", T)
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(api._NoRedirect().redirect_request(None, None, 302, "", {}, M))

    def test_massive_auth_and_no_restricted_cache(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "fixture-token"}):
            self.gateway.fetch("massive", M)
            req = self.requests[0][0]
            self.assertEqual(req.get_header("Authorization"), "Bearer fixture-token")
            self.assertNotIn("fixture-token", json.dumps(self.gateway.health()))
            with self.assertRaisesRegex(api.GatewayError, "Caching not enabled"):
                self.gateway.fetch("massive", M, cache_ttl=1)
            self.gateway.fetch("massive", M)
        self.assertGreaterEqual(self.requests[1][2]-self.requests[0][2], 12)

    def test_retry_after_and_circuit(self):
        self.events = [HTTPError(T, 429, "wait", {"Retry-After": "5"}, BytesIO(b""))]
        self.gateway.fetch("treasury", T)
        self.assertEqual(len(self.requests), 2)
        self.assertGreaterEqual(self.requests[1][2]-self.requests[0][2], 5)
        self.events = [HTTPError(T, 429, "wait", {"Retry-After": "120"}, BytesIO(b""))]
        with self.assertRaises(api.GatewayError):
            self.gateway.fetch("treasury", T)
        self.assertTrue(self.gateway.health()["treasury"]["circuit_open"])
        with self.assertRaisesRegex(api.GatewayError, "circuit open"):
            self.gateway.fetch("treasury", T)
        self.assertEqual(len(self.requests), 3)

    def test_invalid_and_oversized_json(self):
        small = api.Gateway(opener=self.open, clock=self.clock, sleep=self.clock.sleep, max_bytes=32)
        for raw in (b'{"x":NaN}', b'[]', b'x'*33):
            self.events = [raw]
            with self.assertRaises(api.GatewayError):
                small.fetch("treasury", T)

    def test_batch_is_bounded_and_legacy_uses_same_transport(self):
        with self.assertRaises(ValueError):
            asyncio.run(self.gateway.fetch_many([("treasury", T)]*33))
        results = asyncio.run(self.gateway.fetch_many([("treasury", T), ("invalid", T)]))
        self.assertIsInstance(results[0], api.Response)
        self.assertIsInstance(results[1], api.GatewayError)
        with patch.object(market_fetch, "DEFAULT_GATEWAY", self.gateway):
            self.assertEqual(market_fetch.get_json(T), {"data": []})


class SECDownloadTests(unittest.TestCase):
    def test_existing_importer_format_cache_and_tamper(self):
        from gzip import open as gzopen
        cwd = Path.cwd()
        calls = []
        raw = b'{"cik":320193,"facts":{"us-gaap":{}}}'

        def fetch(request, timeout):
            calls.append(request)
            return BytesIO(raw)

        gateway = api.Gateway(opener=fetch)
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)
                with patch.dict(os.environ, {"ATLASPM_SEC_USER_AGENT": "AtlasPM contact@example.org"}):
                    first = sec_download.download_companyfacts("320193", gateway=gateway)
                    second = sec_download.download_companyfacts("320193", gateway=gateway)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].full_url, S)
                path = Path(first["path"])
                self.assertEqual(path.name, "CIK0000320193.json.gz")
                self.assertEqual(second["status"], "LOCAL_CACHE_REUSED")
                self.assertFalse(second["approved_for_model_training"])
                with gzopen(path, "rb") as handle:
                    self.assertEqual(handle.read(), raw)
                with gzopen(path, "wb") as handle:
                    handle.write(b'{"cik":320193,"facts":{}}')
                with self.assertRaisesRegex(api.GatewayError, "content mismatch"):
                    sec_download.download_companyfacts("320193", gateway=gateway)
                self.assertEqual(len(calls), 1)
            finally:
                os.chdir(cwd)

    def test_sec_missing_contact_and_identity_mismatch(self):
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)
                gateway = api.Gateway(opener=lambda *a, **kw: BytesIO(b'{"cik":1,"facts":{}}'))
                with patch.dict(os.environ, {"ATLASPM_SEC_USER_AGENT": ""}):
                    with self.assertRaisesRegex(api.GatewayError, "ATLASPM_SEC_USER_AGENT"):
                        sec_download.download_companyfacts("320193", gateway=gateway)
                with patch.dict(os.environ, {"ATLASPM_SEC_USER_AGENT": "AtlasPM contact@example.org"}):
                    with self.assertRaisesRegex(api.GatewayError, "identity/schema"):
                        sec_download.download_companyfacts("320193", gateway=gateway)
                self.assertFalse(Path("local_data").exists())
            finally:
                os.chdir(cwd)
