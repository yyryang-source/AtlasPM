"""Compatibility import; canonical implementation: atlaspm.engine.mean_variance."""
from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.mean_variance is deprecated; use atlaspm.engine.mean_variance", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.engine.mean_variance")
