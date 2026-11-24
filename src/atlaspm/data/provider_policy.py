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
    "xpressapi": "aggregate_sp_moodys_fitch_ratings",
    "sp_ratings": "credit_ratings", "moodys": "credit_ratings", "fitch": "credit_ratings",
    "dbrs": "supplementary_credit_ratings", "kbra": "supplementary_credit_ratings",
    "investment_banks": "licensed_analyst_research",
    "sp_dji": "index_events", "msci": "index_events", "ftse_russell": "index_events",
    "federal_reserve": "policy_announcements", "finra": "bond_transactions",
    "msrb": "municipal_transactions_and_disclosures",
})
RESTRICTED = frozenset(ROLES) - {"treasury", "sec", "fred", "federal_reserve"}
RIGHTS_ENV = {
    "yahoo_finance": "ATLASPM_YAHOO_NEWS_RIGHTS",
    "bloomberg": "ATLASPM_BLOOMBERG_NEWS_RIGHTS",
    "unusual_whales": "ATLASPM_UW_RIGHTS",
    "ibkr": "ATLASPM_IBKR_MARKET_DATA_RIGHTS",
    **{name: "ATLASPM_" + name.upper() + "_RIGHTS" for name in (
        "xpressapi", "sp_ratings", "moodys", "fitch", "dbrs", "kbra",
        "investment_banks", "sp_dji", "msci", "ftse_russell", "finra", "msrb")},
}


def local_rights(provider, *, retention=False):
    require_enabled(provider)
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
    env = {"unusual_whales":"ATLASPM_UW_MAX_REQUESTS", "xpressapi":"ATLASPM_XPRESSAPI_MAX_REQUESTS"}.get(provider)
    if env is None:
        raise ValueError("Unknown paid feed budget")
    raw = os.environ.get(env, "")
    if not re.fullmatch(r"[0-9]{1,4}", raw) or not 1 <= int(raw) <= 1000:
        raise ValueError(f"Set {env} to a local per-process cap (1..1000)")
    return int(raw)


def inventory():
    return {provider: {"role": role,
                       "connection_verified": False,
                       "rights_attestation_configured": bool(os.environ.get(RIGHTS_ENV[provider]))
                       if provider in RIGHTS_ENV else None,
                       "external_ai_allowed_by_default": False}
            for provider, role in ROLES.items()}


def require_enabled(provider):
    """Explicit opt-out for existing callers; no credentials means no connection."""
    value = os.environ.get("ATLASPM_ENABLE_" + provider.upper())
    if value is not None and value != "1":
        raise ValueError("Provider disabled by local configuration")
