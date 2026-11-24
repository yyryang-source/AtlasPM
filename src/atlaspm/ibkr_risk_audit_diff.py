"""Backward-compatible module path; canonical implementation: atlaspm.monitoring.ibkr_risk_audit_diff."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.ibkr_risk_audit_diff is deprecated; use atlaspm.monitoring.ibkr_risk_audit_diff", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.monitoring.ibkr_risk_audit_diff")
