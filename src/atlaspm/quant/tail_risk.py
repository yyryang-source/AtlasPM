"""Deprecated import path; canonical source: atlaspm.risk.tail_risk.

Retained as a module-object alias for old callers and patch targets.
"""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.quant.tail_risk is deprecated; use atlaspm.risk.tail_risk", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.risk.tail_risk")
