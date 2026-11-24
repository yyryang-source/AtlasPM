"""Compatibility import; canonical implementation: atlaspm.engine.portfolio."""
from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.quant.portfolio is deprecated; use atlaspm.engine.portfolio", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.engine.portfolio")
