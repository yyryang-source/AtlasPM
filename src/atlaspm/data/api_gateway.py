"""Bounded read-only HTTPS JSON gateway. No broker, training or order permissions.

Process-local limits; one serial request stream per provider. Only approved
hosts, no redirects, no credentials in URL or emitted diagnostics.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
import os
import re
from threading import Lock
import time
from types import MappingProxyType
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from atlaspm.data.provider_policy import local_rights, request_budget, inventory


class GatewayError(ValueError):
    """Sanitized provider failure; never include raw URLs or response bodies."""


@dataclass(frozen=True, slots=True)
class Provider:
    host: str
    interval: float
    key_env: str | None
    rights: str
    cache_allowed: bool = False


PROVIDERS = MappingProxyType({
    "treasury": Provider("api.fiscaldata.treasury.gov", 0.5, None, "public_government_data", True),
    "sec": Provider("data.sec.gov", 0.5, None, "public_filing_data_review_required", True),
    "massive": Provider("api.massive.com", 12.0, "MASSIVE_API_KEY", "subscription_review_required"),
    "fred": Provider("api.stlouisfed.org", 1.0, "FRED_API_KEY", "series_specific_rights_review_required"),
    "unusual_whales": Provider("api.unusualwhales.com", 2.0, "UW_TOKEN", "operator_attested_not_independently_verified"),
})


# Fixed destinations; no model-provided URL, tool, or broker route.
MODEL_ENDPOINTS = MappingProxyType({
    "openai": ("https://api.openai.com/v1/responses", "OPENAI_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/models/", "GEMINI_API_KEY"),
})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _strict_json(body):
    def reject(value):
        raise ValueError("Nonfinite JSON constant")
    obj = json.loads(body, parse_constant=reject)
    if not isinstance(obj, dict):
        raise ValueError("Expected JSON object")
    return obj


@dataclass(frozen=True, slots=True)
class Response:
    provider: str
    request_id: str
    fetched_at: str
    body: bytes
    cached: bool = False

    def data(self):
        return _strict_json(self.body)

    def evidence(self):
        return {
            "provider": self.provider, "request_id": self.request_id,
            "retrieved_at_utc": self.fetched_at, "sha256": sha256(self.body).hexdigest(),
            "bytes": len(self.body), "cache_hit": self.cached,
            "rights_status": (PROVIDERS[self.provider].rights if self.provider in PROVIDERS
                              else "external_processing_review_required"),
            "point_in_time_verified": False, "approved_for_model_training": False,
            "approved_for_backtest": False, "live_execution_enabled": False,
        }


class _State:
    def __init__(self):
        self.lock = Lock()
        self.next_request = 0.0
        self.blocked_until = 0.0
        self.consecutive_failures = 0
        self.calls = self.failures = self.cache_hits = self.received_bytes = 0
        self.last_status = "NOT_CONTACTED"


class Gateway:
    """Injectable clock/transport for offline tests; default uses verified TLS."""
    def __init__(self, *, opener=None, clock=time.monotonic, sleep=time.sleep,
                 max_bytes=32 * 1024 * 1024, cache_bytes=8 * 1024 * 1024,
                 attempts=3):
        if (type(max_bytes) is not int or not 1 <= max_bytes <= 64 * 1024 * 1024
                or type(cache_bytes) is not int or not 0 <= cache_bytes <= 32 * 1024 * 1024
                or type(attempts) is not int or not 1 <= attempts <= 3):
            raise ValueError("Invalid gateway resource limits")
        self._open = opener if opener is not None else build_opener(_NoRedirect()).open
        self._clock, self._sleep = clock, sleep
        self._max_bytes, self._cache_bytes, self._attempts = max_bytes, cache_bytes, attempts
        self._states = {name: _State() for name in (*PROVIDERS, *MODEL_ENDPOINTS)}
        self._cache = OrderedDict()
        self._cache_lock = Lock()
        self._cache_size = 0

    @staticmethod
    def _request(provider, url, key):
        spec = PROVIDERS.get(provider)
        if spec is None:
            raise GatewayError("Unknown provider")
        if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in url):
            raise GatewayError("Invalid provider URL")
        try:
            p = urlsplit(url)
            query = parse_qsl(p.query, keep_blank_values=True, max_num_fields=64)
        except ValueError:
            raise GatewayError("Invalid provider URL") from None
        if (p.scheme != "https" or p.netloc != spec.host or not p.path.startswith("/")
                or p.fragment or any(k.casefold().replace("_", "").replace("-", "") in
                   {"apikey", "key", "token", "accesstoken", "authorization", "password", "secret"}
                   for k, _ in query)):
            raise GatewayError("Blocked external provider URL or URL credential")
        if provider == "massive" and not (p.path == "/v3/reference/options/contracts" or
                re.fullmatch(r"/v2/aggs/ticker/O:[A-Z0-9.]+/range/[0-9]+/(minute|day)/[0-9-]+/[0-9-]+", p.path)):
            raise GatewayError("Massive restricted to optional historical options endpoints")
        if provider == "unusual_whales":
            local_rights(provider)
            if not (p.path in {"/api/option-trades/flow-alerts", "/api/market/market-tide"} or
                    re.fullmatch(r"/api/stock/[A-Z0-9.]+/(greek-exposure|net-prem-ticks|option-contracts|iv-rank|volatility/term-structure)", p.path) or
                    re.fullmatch(r"/api/darkpool/[A-Z0-9.]+", p.path)):
                raise GatewayError("Endpoint outside designated options feed scope")
        headers = {"User-Agent": "AtlasPM research gateway", "Accept": "application/json",
                   "Accept-Encoding": "identity"}
        if spec.key_env:
            token = key if key is not None else os.environ.get(spec.key_env, "").strip()
            if not isinstance(token, str) or not token or any(not 33 <= ord(c) <= 126 for c in token):
                raise GatewayError(f"{spec.key_env} missing or invalid")
            if provider == "fred":
                if p.path != "/fred/series/observations":
                    raise GatewayError("FRED endpoint not enabled")
                # Version 1 requires a query credential. Inject only after
                # checking the public URL; do not log/hash the authenticated URL.
                url = urlunsplit((p.scheme, p.netloc, p.path,
                                  urlencode(query + [("api_key", token)]), ""))
            else:
                headers["Authorization"] = "Bearer " + token
        elif key is not None:
            raise GatewayError("Unexpected credential for public provider")
        if provider == "sec":
            agent = os.environ.get("ATLASPM_SEC_USER_AGENT", "").strip()
            if not agent or "@" not in agent or any(not 32 <= ord(c) <= 126 for c in agent):
                raise GatewayError("Set ATLASPM_SEC_USER_AGENT to organization and contact email")
            headers["User-Agent"] = agent
        return Request(url, headers=headers, method="GET")

    def _cached(self, key, ttl):
        with self._cache_lock:
            item = self._cache.get(key)
            if item is None:
                return None
            created, response = item
            if self._clock() - created > ttl:
                self._cache_size -= len(response.body)
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return Response(response.provider, response.request_id, response.fetched_at, response.body, True)

    def _remember(self, key, response):
        if len(response.body) > self._cache_bytes or not self._cache_bytes:
            return
        with self._cache_lock:
            old = self._cache.pop(key, None)
            if old:
                self._cache_size -= len(old[1].body)
            while self._cache and (self._cache_size + len(response.body) > self._cache_bytes
                                   or len(self._cache) >= 128):
                _, (_, discarded) = self._cache.popitem(last=False)
                self._cache_size -= len(discarded.body)
            self._cache[key] = (self._clock(), response)
            self._cache_size += len(response.body)

    @staticmethod
    def _retry_after(headers):
        value = headers.get("Retry-After", "") if headers else ""
        if value.isascii() and value.isdecimal() and len(value) <= 10:
            return float(value)
        try:
            stamp = parsedate_to_datetime(value)
            return max(0.0, (stamp - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def fetch(self, provider, url, *, key=None, cache_ttl=0):
        """GET one JSON object; optional public-only bounded in-memory cache."""
        if type(cache_ttl) is not int or not 0 <= cache_ttl <= 3600:
            raise ValueError("Cache TTL must be 0..3600 seconds")
        request = self._request(provider, url, key)
        spec = PROVIDERS[provider]
        if cache_ttl and not spec.cache_allowed:
            raise GatewayError("Caching not enabled for restricted provider")
        request_id = sha256((provider + "\n" + url).encode()).hexdigest()
        state = self._states[provider]
        with state.lock:
            if cache_ttl:
                cached = self._cached(request_id, cache_ttl)
                if cached is not None:
                    state.cache_hits += 1
                    return cached
            if self._clock() < state.blocked_until:
                raise GatewayError(f"{provider}: circuit open; retry after cooldown")
            for attempt in range(self._attempts):
                if provider == "unusual_whales" and state.calls >= request_budget(provider):
                    raise GatewayError("Unusual Whales local request budget exhausted")
                wait = state.next_request - self._clock()
                if wait > 0:
                    self._sleep(wait)
                state.next_request = self._clock() + spec.interval
                state.calls += 1
                retry_delay = 0.0
                try:
                    with self._open(request, timeout=45) as response:
                        raw = response.read(self._max_bytes + 1)
                    state.received_bytes += len(raw)
                    if len(raw) > self._max_bytes:
                        raise GatewayError("Provider response exceeds configured byte limit")
                    _strict_json(raw)
                    result = Response(provider, request_id, datetime.now(UTC).isoformat(), raw)
                except HTTPError as exc:
                    status = exc.code
                    retry_delay = self._retry_after(exc.headers)
                    exc.close()
                    state.failures += 1
                    state.last_status = f"HTTP_{status}"
                    if status not in (429, 500, 502, 503, 504):
                        # Auth, permission and redirect failures must never fail over.
                        if status in (401, 403):
                            state.blocked_until = self._clock() + 60
                        raise GatewayError(f"{provider}: HTTP {status}") from None
                except (URLError, TimeoutError, OSError):
                    state.failures += 1
                    state.last_status = "NETWORK_ERROR"
                except (ValueError, UnicodeError, RecursionError):
                    state.failures += 1
                    state.last_status = "INVALID_RESPONSE"
                    raise GatewayError(f"{provider}: invalid or oversized JSON response") from None
                else:
                    state.consecutive_failures = 0
                    state.last_status = "SUCCESS"
                    if cache_ttl:
                        self._remember(request_id, result)
                    return result
                state.consecutive_failures += 1
                retry_delay = max(retry_delay, 2 ** attempt)
                if retry_delay > 30 or state.consecutive_failures >= 3:
                    state.blocked_until = self._clock() + max(retry_delay, 60)
                    break
                state.next_request = max(state.next_request, self._clock() + retry_delay)
            raise GatewayError(f"{provider}: {state.last_status}; retry budget exhausted") from None

    def post_model(self, provider, model, payload, *, allow_external=False):
        """One explicitly approved inference POST. Never retry an ambiguous billable call."""
        if allow_external is not True or os.environ.get("ATLASPM_ALLOW_EXTERNAL_AI") != "1":
            raise GatewayError("External AI requires explicit local egress approval")
        if provider not in MODEL_ENDPOINTS:
            raise GatewayError("Unsupported external model provider")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", model):
            raise GatewayError("Invalid configured model identifier")
        allowed = {
            "openai": {"model", "instructions", "input", "max_output_tokens", "store", "text"},
            "anthropic": {"model", "system", "messages", "max_tokens"},
            "gemini": {"systemInstruction", "contents", "generationConfig"},
        }[provider]
        if not isinstance(payload, dict) or not set(payload) <= allowed:
            raise GatewayError("Unexpected model request fields; tools are not supported")
        if provider != "gemini" and payload.get("model") != model:
            raise GatewayError("Model request identifier mismatch")
        if provider == "openai" and payload.get("store") is not False:
            raise GatewayError("OpenAI response storage must be disabled")
        url, key_env = MODEL_ENDPOINTS[provider]
        token = os.environ.get(key_env, "").strip()
        if not token or any(not 33 <= ord(c) <= 126 for c in token):
            raise GatewayError(f"{key_env} missing or invalid")
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Accept-Encoding": "identity", "User-Agent": "AtlasPM research gateway"}
        if provider == "gemini":
            url += model + ":generateContent"
            headers["x-goog-api-key"] = token
        else:
            headers["Authorization"] = "Bearer " + token
            if provider == "anthropic":
                version = os.environ.get("ATLASPM_ANTHROPIC_API_VERSION", "2023-06-01")
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", version):
                    raise GatewayError("Invalid Anthropic API version")
                headers["anthropic-version"] = version
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise GatewayError("Invalid model request JSON") from None
        if len(body) > 65536:
            raise GatewayError("Model request exceeds 64 KiB")
        request_id = sha256(provider.encode() + b"\n" + body).hexdigest()
        state = self._states[provider]
        with state.lock:
            if self._clock() < state.blocked_until:
                raise GatewayError(f"{provider}: circuit open")
            # Conservative per-process call cap, not a monetary limit.
            if state.calls >= 8:
                raise GatewayError(f"{provider}: process model call budget exhausted")
            wait = state.next_request - self._clock()
            if wait > 0:
                self._sleep(wait)
            state.next_request = self._clock() + 2
            state.calls += 1
            try:
                with self._open(Request(url, data=body, headers=headers, method="POST"), timeout=45) as response:
                    raw = response.read(min(self._max_bytes, 2 * 1024 * 1024) + 1)
                state.received_bytes += len(raw)
                if len(raw) > min(self._max_bytes, 2 * 1024 * 1024):
                    raise GatewayError("Model response exceeds size limit")
                _strict_json(raw)
            except HTTPError as exc:
                code = exc.code
                delay = self._retry_after(exc.headers)
                exc.close()
                state.failures += 1
                state.last_status = f"HTTP_{code}"
                state.blocked_until = self._clock() + max(60, delay)
                raise GatewayError(f"{provider}: HTTP {code}; no automatic retry") from None
            except (OSError, ValueError, RecursionError):
                state.failures += 1
                state.last_status = "MODEL_RESPONSE_ERROR"
                state.blocked_until = self._clock() + 60
                raise GatewayError(f"{provider}: model request failed; billing status unknown") from None
            state.last_status = "SUCCESS"
            return Response(provider, request_id, datetime.now(UTC).isoformat(), raw)

    async def fetch_many(self, requests):
        """At most 32 GETs, at most three active worker threads for this batch.

        Cancellation does not interrupt an already-running HTTPS request.
        Per-provider serialization and rate limits also apply to sync callers.
        """
        if not isinstance(requests, (list, tuple)) or not 1 <= len(requests) <= 32:
            raise ValueError("Batch must contain 1..32 (provider, URL) pairs")
        if any(not isinstance(pair, (tuple, list)) or len(pair) != 2
               or any(not isinstance(value, str) for value in pair) for pair in requests):
            raise ValueError("Expected provider and URL strings")
        pairs = [tuple(pair) for pair in requests]
        unique = list(dict.fromkeys(pairs))
        results = {}
        retained = 0
        # Bound retained response bytes and transient in-flight responses.
        # Duplicate requests within this batch share an immutable response.
        for start in range(0, len(unique), 3):
            chunk = unique[start:start+3]
            batch = await asyncio.gather(*(asyncio.to_thread(self.fetch, *pair) for pair in chunk),
                                         return_exceptions=True)
            for pair, item in zip(chunk, batch):
                if isinstance(item, Response):
                    if retained + len(item.body) > self._max_bytes:
                        item = GatewayError("Batch retained byte budget exceeded")
                    else:
                        retained += len(item.body)
                results[pair] = item
            del batch
        return [results[pair] for pair in pairs]

    def health(self):
        result = {}
        for name, state in self._states.items():
            with state.lock:
                spec = PROVIDERS.get(name)
                key_env = spec.key_env if spec else MODEL_ENDPOINTS[name][1]
                result[name] = {
                    "calls": state.calls, "failed_attempts": state.failures,
                    "cache_hits": state.cache_hits, "received_bytes": state.received_bytes,
                    "last_status": state.last_status,
                    "circuit_open": self._clock() < state.blocked_until,
                    "credential_configured": bool(os.environ.get(key_env)) if key_env else
                        (bool(os.environ.get("ATLASPM_SEC_USER_AGENT")) if name == "sec" else True),
                    "rights_status": spec.rights if spec else "external_processing_review_required",
                    "cost_usd": None,  # do not infer subscription/overage costs
                }
        return result


# Shared by the existing downloader and new adapters; no network at import.
DEFAULT_GATEWAY = Gateway()


def main():
    from argparse import ArgumentParser
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-treasury", action="store_true",
                        help="Explicitly perform one public FiscalData request")
    args = parser.parse_args()
    report = {}
    if args.smoke_treasury:
        url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
               "accounting/od/treasury_securities_auctions_v2?page%5Bsize%5D=1")
        response = DEFAULT_GATEWAY.fetch("treasury", url)
        rows = response.data().get("data")
        if not isinstance(rows, list) or len(rows) != 1:
            raise GatewayError("Treasury smoke response missing requested row")
        report["treasury_smoke"] = response.evidence()
    report["providers"] = DEFAULT_GATEWAY.health()
    report["designated_sources"] = inventory()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
