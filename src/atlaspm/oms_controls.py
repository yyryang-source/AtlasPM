"""Backward-compatible module path; canonical implementation: atlaspm.execution.oms_controls."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.oms_controls is deprecated; use atlaspm.execution.oms_controls", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.execution.oms_controls")
