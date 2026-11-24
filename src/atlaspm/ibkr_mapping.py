"""Backward-compatible module path; canonical implementation: atlaspm.execution.ibkr_mapping."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.ibkr_mapping is deprecated; use atlaspm.execution.ibkr_mapping", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.execution.ibkr_mapping")
