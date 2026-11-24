"""Backward-compatible module path; canonical implementation: atlaspm.execution.institutional_rebalance."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.institutional_rebalance is deprecated; use atlaspm.execution.institutional_rebalance", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.execution.institutional_rebalance")
