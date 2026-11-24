"""Backward-compatible module path; canonical implementation: atlaspm.execution.paper_oms."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.paper_oms is deprecated; use atlaspm.execution.paper_oms", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.execution.paper_oms")
