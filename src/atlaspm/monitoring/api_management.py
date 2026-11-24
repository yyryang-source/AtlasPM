"""Read-only local API management inventory and opt-in connection check.

Secrets remain in the process environment (or supplied by a local OS vault
launcher). No plaintext credential file, no key display, no automatic calls.
"""
from __future__ import annotations
from datetime import UTC, datetime
import os
from atlaspm.data.api_gateway import DEFAULT_GATEWAY, PROVIDERS, MODEL_ENDPOINTS
from atlaspm.data.provider_policy import ROLES, RIGHTS_ENV, require_enabled

_BLOCKED = {"yahoo_finance","bloomberg","sp_ratings","moodys","fitch","dbrs","kbra",
            "investment_banks","sp_dji","msci","ftse_russell","finra","msrb"}


def inventory(gateway=DEFAULT_GATEWAY):
    health=gateway.health()
    result=[]
    for name in sorted(set(ROLES)|set(MODEL_ENDPOINTS)):
        state=health.get(name,{})
        env=PROVIDERS[name].key_env if name in PROVIDERS else MODEL_ENDPOINTS.get(name,("",None))[1]
        if name=="sec":
            env="ATLASPM_SEC_USER_AGENT"
        configured=bool(os.environ.get(env)) if env else None
        if name=="xpressapi":
            configured=bool(os.environ.get("XPRESSAPI_TOKEN") or
                            (os.environ.get("XPRESSAPI_USERNAME") and os.environ.get("XPRESSAPI_PASSWORD")))
        rights=bool(os.environ.get(RIGHTS_ENV[name])) if name in RIGHTS_ENV else None
        enabled=os.environ.get("ATLASPM_ENABLE_"+name.upper(),"1")=="1"
        ready=enabled and name not in _BLOCKED and configured is not False and rights is not False
        if name in {"ibkr","hf"}:
            ready=False  # local session/archive evidence not known in this process
        expiry=None
        if env and name=="xpressapi" and os.environ.get("XPRESSAPI_TOKEN_EXPIRES_AT"):
            try:
                t=datetime.fromisoformat(os.environ["XPRESSAPI_TOKEN_EXPIRES_AT"])
                expiry=t.utcoffset() is not None and t>datetime.now(UTC)
            except ValueError:
                expiry=False
            ready=ready and expiry is True
        result.append({"provider":name,"role":ROLES.get(name,"external_ai_research"),
            "mode":"BLOCKED_WIRE_SPEC_OR_ENTITLEMENT" if name in _BLOCKED else
                   "LOCAL_SESSION_OR_ARCHIVE" if name in {"ibkr","hf"} else "IMPLEMENTED_ADAPTER",
            "enabled_by_configuration":enabled,"ready_for_manual_attempt":ready,
            "credential_env":env,"credential_configured":configured,"rights_attested":rights,
            "token_expiry_operator_valid":expiry,"auth_mode":
                "local_gateway" if name=="ibkr" else "licensed_file_or_pending" if name in _BLOCKED else
                "bearer" if name in {"xpressapi","unusual_whales","massive","openai","anthropic"} else
                "api_key" if name in {"fred","gemini"} else "contact_header" if name=="sec" else "none",
            "calls":state.get("calls",0),"success_rate":state.get("success_rate"),
            "last_success_utc":state.get("last_success_utc"),"latency_seconds":state.get("last_latency_seconds"),
            "last_status":state.get("last_status","NOT_CONTACTED"),"circuit_open":state.get("circuit_open",False),
            "cost_usd":None,"subscription_quota_verified":False,"connection_verified":False})
    return result


def public_smoke(gateway=DEFAULT_GATEWAY):
    """Explicit single public Treasury check; no paid endpoint or broker."""
    require_enabled("treasury")
    response=gateway.fetch("treasury","https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
                           "accounting/od/treasury_securities_auctions_v2?page%5Bsize%5D=1")
    rows=response.data().get("data")
    if not isinstance(rows,list) or len(rows)!=1:
        raise ValueError("Treasury smoke schema failure")
    return {"status":"PUBLIC_RESPONSE_SCHEMA_CHECKED","evidence":response.evidence()}
