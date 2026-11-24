"""Deprecated import path; canonical source: atlaspm.research.ml_dynamic_rebalance.

Retained as a module-object alias for old callers and patch targets.
"""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.quant.ml_dynamic_rebalance is deprecated; use atlaspm.research.ml_dynamic_rebalance", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.research.ml_dynamic_rebalance")
