"""Designated provider ownership and local rights attestations.

A local attestation is an operator prerequisite, not independent proof of a
contract or entitlement. Restricted feeds never enter external AI by default.
"""
from __future__ import annotations

import os
import re
from types import MappingProxyType

ROLES = MappingProxyType({
    "yahoo_finance": "issuer_news",
    "bloomberg": "global_news_and_macro_events",
    "unusual_whales": "options_flow_and_darkpool",
    "ibkr": "primary_market_data_and_only_broker",
    "massive": "optional_historical_options",
    "treasury": "official_treasury_history",
    "sec": "official_filings",
    "fred": "macro_vintages",
    "hf": "existing_historical_archive",
})
RESTRICTED = frozenset({"yahoo_finance", "bloomberg", "unusual_whales", "ibkr", "massive", "hf"})
RIGHTS_ENV = {
    "yahoo_finance": "ATLASPM_YAHOO_NEWS_RIGHTS",
    "bloomberg": "ATLASPM_BLOOMBERG_NEWS_RIGHTS",
    "unusual_whales": "ATLASPM_UW_RIGHTS",
    "ibkr": "ATLASPM_IBKR_MARKET_DATA_RIGHTS",
}


def local_rights(provider, *, retention=False):
    if provider not in RIGHTS_ENV:
        raise ValueError("Unknown restricted feed rights scope")
    scope = RIGHTS_ENV[provider]
    # Use a nonsecret local contract reference, not contract text or API key.
    reference = os.environ.get(scope, "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", reference):
        raise ValueError(f"Set {scope} only after confirming applicable feed rights")
    if retention and os.environ.get(scope + "_RETENTION") != "1":
        raise ValueError(f"Local retention requires {scope}_RETENTION=1")
    return {"rights_status": "operator_attested_not_independently_verified",
            "external_ai_allowed": False, "retention_attested": retention}


def request_budget(provider):
    if provider != "unusual_whales":
        raise ValueError("Unknown paid feed budget")
    raw = os.environ.get("ATLASPM_UW_MAX_REQUESTS", "")
    if not re.fullmatch(r"[0-9]{1,4}", raw) or not 1 <= int(raw) <= 1000:
        raise ValueError("Set ATLASPM_UW_MAX_REQUESTS to a local per-process cap (1..1000)")
    return int(raw)


def inventory():
    return {provider: {"role": role,
                       "connection_verified": False,
                       "rights_attestation_configured": bool(os.environ.get(RIGHTS_ENV[provider]))
                       if provider in RIGHTS_ENV else None,
                       "external_ai_allowed_by_default": provider not in RESTRICTED}
            for provider, role in ROLES.items()}
