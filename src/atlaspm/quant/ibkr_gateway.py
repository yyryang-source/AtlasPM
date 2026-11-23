"""IBKR Client Portal Gateway read-only account collection.

Public collection uses GET over loopback HTTPS. The shared JSON transport
also permits the explicitly scoped What-If preview. No order submission.
Raw snapshots are private, not reconciled normalized broker permissions.
"""

from __future__ import annotations

import json
import re
import ssl
import time
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener,
)


DEFAULT_GATEWAY_URL = "https://localhost:5000/v1/api"
MAX_RESPONSE_BYTES = 3_000_000
SLOW_GETS = frozenset({
    "/iserver/account/orders", "/iserver/account/trades", "/iserver/account/pnl/partitioned",
    "/portfolio/accounts", "/portfolio/subaccounts",
})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Gateway redirected request; refusing a redirected account endpoint")


class GatewayHTTPError(ValueError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Gateway HTTP {status}; verify its browser session and account access")


def validate_gateway_url(url: str) -> str:
    """Accept ONLY the IBKR Gateway API root on loopback HTTPS."""
    if not isinstance(url, str):
        raise ValueError("Gateway URL must be a string")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in {"localhost", "127.0.0.1"}
        or parts.username is not None or parts.password is not None
        or parts.query or parts.fragment
        or parts.path.rstrip("/") != "/v1/api"
        or parts.port is None or not (1 <= parts.port <= 65535)
    ):
        raise ValueError(
            "Gateway URL must be loopback HTTPS with /v1/api, e.g. "
            "https://localhost:5000/v1/api"
        )
    return f"https://{parts.hostname}:{parts.port}/v1/api"


def _json_no_nan(data: bytes) -> Any:
    def invalid_constant(x: str):
        raise ValueError(f"Invalid non-JSON number: {x}")
    return json.loads(data.decode("utf-8"), parse_constant=invalid_constant)


class LocalGatewayTransport:
    """Loopback HTTPS client with verified certificates and no proxies."""

    def __init__(
        self, base_url: str = DEFAULT_GATEWAY_URL, *, ca_file: str | None = None,
        timeout_seconds: float = 8.0,
    ):
        self.base_url = validate_gateway_url(base_url)
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
            raise ValueError("timeout_seconds must be numeric")
        if not isfinite(timeout_seconds) or not (1 <= timeout_seconds <= 30):
            raise ValueError("timeout_seconds must be between 1 and 30")
        self.timeout_seconds = float(timeout_seconds)
        self._pacing_lock = Lock()
        self._next_request = 0.0
        self._next_get: dict[str, float] = {}
        # Never use an unverified TLS context, even on localhost. Users can
        # configure the Gateway with a locally trusted certificate instead.
        context = ssl.create_default_context(
            cafile=str(Path(ca_file).expanduser()) if ca_file else None
        )
        self.opener = build_opener(
            ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect()
        )

    def get(self, endpoint: str) -> Any:
        return self._request(endpoint)

    def _request(
        self, endpoint: str, *, params: dict[str, str] | None = None,
        preview: dict[str, Any] | None = None,
    ) -> Any:
        if not isinstance(endpoint, str) or not endpoint.startswith("/"):
            raise ValueError("Gateway endpoint must be an absolute API subpath")
        if (
            "//" in endpoint or "?" in endpoint or "#" in endpoint
            or ".." in endpoint or "\\" in endpoint or "://" in endpoint
        ):
            raise ValueError("Gateway endpoint contains invalid characters")
        if preview is not None and (
            params is not None
            or not re.fullmatch(r"/iserver/account/[A-Za-z0-9_-]{1,64}/orders/whatif", endpoint)
        ):
            raise ValueError("This transport only allows POST to the order-preview endpoint")
        target = self.base_url + endpoint
        if params:
            target += "?" + urlencode(params)
        headers = {"Accept": "application/json", "User-Agent": "AtlasPM-readonly/0.4"}
        if preview is not None:
            headers["Content-Type"] = "application/json"
        req = Request(
            target, headers=headers, method="POST" if preview is not None else "GET",
            data=json.dumps(preview, allow_nan=False).encode() if preview is not None else None,
        )
        return self._send_json(req)

    def _send_json(self, req: Request) -> Any:
        endpoint = urlsplit(req.full_url).path.removeprefix("/v1/api")
        slow_get = req.get_method() == "GET" and endpoint in SLOW_GETS
        try:
            with self._pacing_lock:
                due = max(self._next_request, self._next_get.get(endpoint, 0.0) if slow_get else 0.0)
                remaining = due - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                started = time.monotonic()
                self._next_request = started + 0.11
                if slow_get:
                    self._next_get[endpoint] = started + 5.1
                with self.opener.open(req, timeout=self.timeout_seconds) as resp:
                    data = resp.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise GatewayHTTPError(exc.code) from None
        except (URLError, TimeoutError, ssl.SSLError) as exc:
            raise ValueError(
                "Cannot read local IBKR Gateway using verified TLS. "
                "Confirm Gateway is running, browser login completed, and a "
                "valid localhost certificate is trusted or provided via --ca-file."
            ) from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("Gateway response exceeds size limit")
        try:
            return _json_no_nan(data)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("IBKR Gateway returned invalid UTF-8 JSON") from exc


class FixtureGatewayTransport:
    """Zero-network transport for testing the IBKR Client Portal API workflow."""

    def __init__(self, fixture: dict[str, Any]):
        if not isinstance(fixture, dict) or not isinstance(fixture.get("responses"), dict):
            raise ValueError("Fixture must contain responses object")
        self.responses = fixture["responses"]
        self.called: list[str] = []

    def get(self, endpoint: str) -> Any:
        self.called.append(endpoint)
        if endpoint not in self.responses:
            raise ValueError(f"Synthetic fixture missing endpoint {endpoint}")
        return self.responses[endpoint]


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be positive integer")
    return value


def _num(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def collect_ibkr_readonly_account(
    transport: Any, *, account_id: str, fixture_mode: bool = False,
) -> dict[str, Any]:
    """GET auth, account list, USD ledger, and portfolio2 positions.

    The result deliberately preserves IBKR's raw fields. Reconstructing a
    self-financing margin/collateral ledger from IBKR responses requires broker
    documentation and additional checks; this is NOT a trustworthy normalized
    margin/buying-power snapshot yet.
    """
    if (
        not isinstance(account_id, str) or not account_id
        or len(account_id) > 64
        or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in account_id)
    ):
        raise ValueError("account_id may contain only letters, numbers, dash and underscore")
    auth = transport.get("/iserver/auth/status")
    if not isinstance(auth, dict):
        raise ValueError("IBKR auth/status returned a non-object")
    if auth.get("authenticated") is not True or auth.get("connected") is not True:
        raise ValueError("IBKR local Gateway session is not authenticated and connected")

    accounts = transport.get("/portfolio/accounts")
    if not isinstance(accounts, list) or not all(isinstance(a, dict) for a in accounts):
        raise ValueError("IBKR portfolio/accounts must return an account array")
    match = [
        acc for acc in accounts
        if acc.get("accountId", acc.get("id")) == account_id
    ]
    if len(match) != 1:
        raise ValueError("Specified IBKR account not found uniquely in accessible accounts")
    if match[0].get("currency") not in {"USD"}:
        raise ValueError("AtlasPM raw collection currently requires a USD base-currency account")

    quoted_id = quote(account_id, safe="")
    raw_ledger = transport.get(f"/portfolio/{quoted_id}/ledger")
    raw_positions = transport.get(f"/portfolio2/{quoted_id}/positions")
    if not isinstance(raw_ledger, dict):
        raise ValueError("IBKR portfolio ledger must be an object")
    if not isinstance(raw_positions, list) or not all(
        isinstance(p, dict) for p in raw_positions
    ):
        raise ValueError("IBKR portfolio2 positions must be an array")
    # IBKR frequently provides USD and BASE. Prefer explicit USD only.
    usd = raw_ledger.get("USD")
    if not isinstance(usd, dict):
        raise ValueError("No USD ledger block; cannot infer net liquidation")
    if "settledcash" not in usd or "netliquidationvalue" not in usd:
        raise ValueError("USD ledger lacks settledcash or netliquidationvalue")
    settled = _num(usd["settledcash"], "USD.settledcash")
    nlv = _num(usd["netliquidationvalue"], "USD.netliquidationvalue")
    if nlv <= 0:
        raise ValueError("IBKR raw net liquidation must be positive")
    if any(p.get("currency") not in {"USD"} for p in raw_positions):
        raise ValueError("Non-USD or unidentifiable position currency: no automatic FX conversion")
    if any("conid" not in p or "position" not in p for p in raw_positions):
        raise ValueError("IBKR position missing contract ID or position quantity")
    for position in raw_positions:
        _positive_int(position["conid"], "position.conid")
        _num(position["position"], "position.position")
    class_counts: dict[str, int] = {}
    for pos in raw_positions:
        asset_class = str(pos.get("assetClass", pos.get("secType", "UNKNOWN")))
        class_counts[asset_class] = class_counts.get(asset_class, 0) + 1

    return {
        "method": "ibkr_client_portal_gateway_readonly_raw_capture",
        "source": "synthetic_ibkr_fixture" if fixture_mode else "local_authenticated_ibkr_gateway",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "account_id": account_id,
        "session_reported_authenticated": True,
        "gateway_tls_certificate_validated": not fixture_mode,
        "live_execution_enabled": False,
        "broker_margin_or_collateral_verified": False,
        "ready_for_institutional_risk_gate": False,
        "USD_ledger_summary": {
            "settled_cash_ibkr_field": settled,
            "net_liquidation_ibkr_field": nlv,
            "margin_debt_separately_verified": False,
            "treasury_collateral_verified": False,
        },
        "position_count": len(raw_positions),
        "asset_class_counts": class_counts,
        "raw": {
            "auth_status": auth,
            "selected_account": match[0],
            "USD_ledger": raw_ledger,
            "positions": raw_positions,
        },
        "warnings": [
            "Only read-only IBKR Web API GET endpoints are implemented. No orders are sent.",
            "The full raw output contains PRIVATE ACCOUNT DATA: store locally, never commit, paste or share its contents.",
            "An authenticated session and server-returned fields are NOT a verified margin preview.",
            "Account balances, financing debit, unsettled cash, UST collateral and option identifiers are not yet normalized to the AtlasPM ledger.",
            "No margin borrowing, options hedging or portfolio trading is authorized by this report.",
        ],
    }


def redacted_console_summary(report: Mapping[str, Any], output: Path) -> dict[str, Any]:
    account = str(report["account_id"])
    return {
        "written": str(output),
        "method": report["method"],
        "account_hint": "****" + account[-4:],
        "source": report["source"],
        "position_count": report["position_count"],
        "asset_class_counts": report["asset_class_counts"],
        "gateway_tls_certificate_validated": report["gateway_tls_certificate_validated"],
        "session_reported_authenticated": report["session_reported_authenticated"],
        "broker_margin_or_collateral_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Private raw account data was saved locally: do not upload, print or commit it.",
            "This capture does not verify brokerage margin, collateral, or ability to trade.",
        ],
    }


def require_private_output(path: Path, *, project_root: Path | None = None) -> Path:
    """Disallow writing raw portfolio data into tracked repository files."""
    target = path.expanduser().resolve()
    root = (project_root or Path.cwd()).resolve()
    private = (root / "local_data").resolve()
    if not target.is_relative_to(private) or target == private:
        raise ValueError("IBKR raw account capture must be saved under local_data/ only")
    return target
