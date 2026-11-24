"""Backward-compatible module path; canonical implementation: atlaspm.monitoring.audit."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.audit is deprecated; use atlaspm.monitoring.audit", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.monitoring.audit")
