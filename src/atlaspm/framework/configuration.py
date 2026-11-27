"""Strict local runtime configuration; credentials remain local and unreported."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import re
import tomllib

from atlaspm.data.market_db import private_path
from atlaspm.data.provider_policy import ROLES
from atlaspm.data.api_gateway import MODEL_ENDPOINTS, PROVIDERS

DEFAULT_PATH = Path("local_data/atlaspm.toml")
DEFAULTS = {
    "version": 1,
    "runtime": {"cycles": 10, "interval_seconds": 5, "max_memory_mib": 1024,
                "max_database_mib": 1024, "max_log_mib": 4, "log_backups": 3},
    "sources": {"sec": False, "fed": False, "documents": False, "queue": True},
    "research": {"treasury": False, "options": False, "fred_series": [],
                 "news_providers": [], "credit_requests": [], "institutional_requests": [],
                 "fundamental_universe": "", "fundamental_batch": 5},
    "local_ai": {"enabled": False, "model": "", "universe": "", "batch_size": 20},
    "paths": {"checkpoint": "local_data/runs/system.continuous.json"},
    "providers": {},
}
EXAMPLE = '''# Credentials belong in a local .env, never in this TOML file.
version = 1

[runtime]
cycles = 10
interval_seconds = 5
max_memory_mib = 1024
max_database_mib = 1024
max_log_mib = 4
log_backups = 3

[sources]
sec = false
fed = false
documents = false
queue = true

[research]
treasury = false
options = false
fred_series = []
news_providers = []
# Each credit item is scope:identifier_type:identifier:reviewed_ticker,
# e.g. issue:CUSIP9:<licensed CUSIP>:<reviewed ticker>.
credit_requests = []
# Each report item is a stable typed subject=reviewed_ticker,
# e.g. CIK:<ten-digit issuer CIK>=<reviewed ticker>.
institutional_requests = []
fundamental_universe = ""
fundamental_batch = 5

[local_ai]
enabled = false
model = ""
universe = ""
batch_size = 20

[paths]
checkpoint = "local_data/runs/system.continuous.json"

[providers]
# Add explicit provider overrides, e.g. unusual_whales = false.
'''


def validate(value):
    if not isinstance(value, dict) or not set(value) <= set(DEFAULTS):
        raise ValueError("CONFIG_UNKNOWN_SECTION")
    config = deepcopy(DEFAULTS)
    for key, item in value.items():
        if key == "version":
            if type(item) is not int or item != 1:
                raise ValueError("CONFIG_VERSION")
            continue
        if not isinstance(item, dict):
            raise ValueError("CONFIG_SECTION_TYPE")
        permitted = set(ROLES) | set(MODEL_ENDPOINTS) | set(PROVIDERS) if key == "providers" else set(config[key])
        if not set(item) <= permitted:
            raise ValueError("CONFIG_UNKNOWN_SETTING")
        config[key].update(item)
    bounds = {"cycles": (1, 86400), "interval_seconds": (1, 300),
              "max_memory_mib": (64, 65536), "max_database_mib": (1, 1048576),
              "max_log_mib": (1, 64), "log_backups": (1, 10)}
    for key, (low, high) in bounds.items():
        item = config["runtime"][key]
        if type(item) is not int or not low <= item <= high:
            raise ValueError("CONFIG_RUNTIME_BOUND")
    research = config["research"]
    for item in (*config["sources"].values(), *config["providers"].values(), config["local_ai"]["enabled"],
                 research["treasury"], research["options"]):
        if type(item) is not bool:
            raise ValueError("CONFIG_BOOLEAN_REQUIRED")
    ai = config["local_ai"]
    if not isinstance(ai["model"], str) or (ai["model"] and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", ai["model"])):
        raise ValueError("CONFIG_LOCAL_MODEL")
    if type(ai["batch_size"]) is not int or not 1 <= ai["batch_size"] <= 100:
        raise ValueError("CONFIG_BATCH_SIZE")
    if not isinstance(ai["universe"], str):
        raise ValueError("CONFIG_UNIVERSE_PATH")
    if ai["universe"]:
        private_path(Path(ai["universe"]))
    if ai["enabled"] and (not ai["model"] or not ai["universe"]):
        raise ValueError("CONFIG_LOCAL_AI_MODEL_AND_UNIVERSE_REQUIRED")
    for key in ("fred_series", "news_providers", "credit_requests", "institutional_requests"):
        rows = research[key]
        if not isinstance(rows, list) or len(rows) > 16 or any(not isinstance(r, str) or len(r) > 256 for r in rows) or len(set(rows)) != len(rows):
            raise ValueError("CONFIG_RESEARCH_TASK_LIMIT")
    if any(not re.fullmatch(r"[A-Za-z0-9_]{1,80}", s) for s in research["fred_series"]):
        raise ValueError("CONFIG_FRED_SERIES")
    if any(p not in {"yahoo_finance", "bloomberg"} for p in research["news_providers"]):
        raise ValueError("CONFIG_NEWS_PROVIDER")
    for item in research["credit_requests"]:
        fields = item.split(":")
        if len(fields) != 4 or any(not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", v) for v in fields):
            raise ValueError("CONFIG_CREDIT_REQUEST")
        from atlaspm.research.credit_intelligence import _ID_TYPES
        scope, kind, identifier, symbol = fields
        pattern = _ID_TYPES.get(scope, {}).get(kind)
        if pattern is None or not re.fullmatch(pattern, identifier) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", symbol):
            raise ValueError("CONFIG_CREDIT_IDENTITY")
    for item in research["institutional_requests"]:
        subject, sep, symbol = item.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", subject) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", symbol):
            raise ValueError("CONFIG_INSTITUTIONAL_REQUEST")
        from atlaspm.research.institutional_research import _subject
        _subject(subject)
    if not isinstance(research["fundamental_universe"], str):
        raise ValueError("CONFIG_FUNDAMENTAL_UNIVERSE")
    if research["fundamental_universe"]:
        private_path(Path(research["fundamental_universe"]))
    if type(research["fundamental_batch"]) is not int or not 1 <= research["fundamental_batch"] <= 20:
        raise ValueError("CONFIG_FUNDAMENTAL_BATCH")
    checkpoint = config["paths"]["checkpoint"]
    if not isinstance(checkpoint, str) or not checkpoint.endswith(".json"):
        raise ValueError("CONFIG_CHECKPOINT")
    private_path(Path(checkpoint))
    return config


def load(path=DEFAULT_PATH):
    p = private_path(Path(path), exists=True)
    with p.open("rb") as handle:
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise ValueError("CONFIG_SIZE_LIMIT")
    try:
        value = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("CONFIG_INVALID_TOML") from None
    return validate(value)


def read_environment(path):
    """Parse data only: no shell evaluation, expansion, arbitrary environment keys."""
    p = Path(path)
    if p.is_symlink():
        raise ValueError("ENV_REDIRECTED_FILE")
    p = p.resolve()
    root = Path.cwd().resolve()
    if not p.is_relative_to(root) or (p.name != ".env" and not p.is_relative_to(root / "local_data")):
        raise ValueError("ENV_LOCAL_IGNORED_FILE_REQUIRED")
    with p.open("rb") as handle:
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise ValueError("ENV_SIZE_LIMIT")
    credential_keys = {p.key_env for p in PROVIDERS.values() if p.key_env}
    credential_keys.update(v[1] for v in MODEL_ENDPOINTS.values())
    credential_keys.update({"XPRESSAPI_USERNAME", "XPRESSAPI_PASSWORD", "XPRESSAPI_TOKEN_EXPIRES_AT",
                            "FINRA_CLIENT_ID", "FINRA_CLIENT_SECRET", "HF_TOKEN"})
    result = {}
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeError:
        raise ValueError("ENV_ENCODING") from None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", key) or key in result:
            raise ValueError("ENV_INVALID_OR_DUPLICATE_KEY")
        if key not in credential_keys and not key.startswith("ATLASPM_"):
            raise ValueError("ENV_KEY_NOT_ALLOWED")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("ENV_INVALID_QUOTING")
            value = value[1:-1]
        if any(ord(c) < 32 or ord(c) == 127 for c in value) or len(value) > 8192:
            raise ValueError("ENV_INVALID_VALUE")
        if key in {"ATLASPM_ALLOW_PAPER_ORDERS", "ATLASPM_ALLOW_EXTERNAL_AI", "ATLASPM_ALLOW_LIVE_ORDERS", "ATLASPM_LIVE_TRADING"} and value not in {"", "0"}:
            raise ValueError("ENV_EXECUTION_OR_EGRESS_OPTIN_FORBIDDEN")
        result[key] = value
    return result


@contextmanager
def environment(config, *, env_file=None):
    """Scoped settings; existing OS/vault credentials take precedence."""
    overrides = read_environment(env_file) if env_file is not None else {}
    overrides = {k: v for k, v in overrides.items() if k not in os.environ}
    overrides.update({"ATLASPM_ENABLE_" + k.upper(): "1" if v else "0"
                      for k, v in config["providers"].items()})
    # A parent-source opt-out also closes its feed/document routes.
    for provider, aliases in {"sec": ("sec_feed", "sec_document"),
                              "federal_reserve": ("fed_document",)}.items():
        key = "ATLASPM_ENABLE_" + provider.upper()
        value = overrides.get(key, os.environ.get(key))
        if value is not None and value != "1":
            overrides.update({"ATLASPM_ENABLE_" + alias.upper(): "0" for alias in aliases})
    old = {key: os.environ.get(key) for key in overrides}
    try:
        os.environ.update(overrides)
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
