"""Compatibility import; canonical implementation: atlaspm.monitoring.benchmark."""
from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.benchmark is deprecated; use atlaspm.monitoring.benchmark", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.monitoring.benchmark")
