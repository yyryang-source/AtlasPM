"""Backward-compatible module path; canonical implementation: atlaspm.risk.ibkr_multiasset_budget."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.ibkr_multiasset_budget is deprecated; use atlaspm.risk.ibkr_multiasset_budget", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.risk.ibkr_multiasset_budget")
