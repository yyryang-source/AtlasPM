"""Deprecated import path; canonical source: atlaspm.research.ai_stock_ranker_v2.

Retained as a module-object alias for old callers and patch targets.
"""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.selection.ai_stock_ranker_v2 is deprecated; use atlaspm.research.ai_stock_ranker_v2", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.research.ai_stock_ranker_v2")
