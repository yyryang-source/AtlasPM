"""Backward-compatible module path; canonical implementation: atlaspm.quant.ibkr_risk_audit_diff."""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("atlaspm.quant.ibkr_risk_audit_diff")
