"""Backward-compatible module path; canonical implementation: atlaspm.data.historical."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.historical is deprecated; use atlaspm.data.historical", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.data.historical")
