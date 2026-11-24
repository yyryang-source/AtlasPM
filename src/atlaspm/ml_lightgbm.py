"""Compatibility import; canonical implementation: atlaspm.research.ml_lightgbm."""
from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.ml_lightgbm is deprecated; use atlaspm.research.ml_lightgbm", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.research.ml_lightgbm")
