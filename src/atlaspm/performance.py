"""Backward-compatible module path; canonical implementation: atlaspm.monitoring.performance."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.performance is deprecated; use atlaspm.monitoring.performance", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.monitoring.performance")
