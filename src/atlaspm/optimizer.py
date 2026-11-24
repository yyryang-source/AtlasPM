"""Compatibility import; canonical implementation: atlaspm.engine.optimizer."""
from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.optimizer is deprecated; use atlaspm.engine.optimizer", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.engine.optimizer")
