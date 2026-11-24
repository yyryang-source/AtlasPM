"""Backward-compatible module path; canonical implementation: atlaspm.risk.tail_risk."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.tail_risk is deprecated; use atlaspm.risk.tail_risk", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.risk.tail_risk")
