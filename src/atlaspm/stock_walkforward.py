"""Backward-compatible module path; canonical implementation: atlaspm.research.stock_walkforward."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.stock_walkforward is deprecated; use atlaspm.research.stock_walkforward", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.research.stock_walkforward")
