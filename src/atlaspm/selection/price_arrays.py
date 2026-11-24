"""Deprecated import path; canonical source: atlaspm.data.price_arrays.

Retained as a module-object alias for old callers and patch targets.
"""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.selection.price_arrays is deprecated; use atlaspm.data.price_arrays", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.data.price_arrays")
