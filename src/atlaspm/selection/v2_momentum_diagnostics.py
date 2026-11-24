"""Deprecated import path; canonical source: atlaspm.research.v2_momentum_diagnostics.

Retained as a module-object alias for old callers and patch targets.
"""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.selection.v2_momentum_diagnostics is deprecated; use atlaspm.research.v2_momentum_diagnostics", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.research.v2_momentum_diagnostics")
