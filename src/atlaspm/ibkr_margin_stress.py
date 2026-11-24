"""Backward-compatible module path; canonical implementation: atlaspm.risk.ibkr_margin_stress."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.ibkr_margin_stress is deprecated; use atlaspm.risk.ibkr_margin_stress", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.risk.ibkr_margin_stress")
