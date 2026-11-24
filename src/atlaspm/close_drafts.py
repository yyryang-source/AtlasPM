"""Backward-compatible module path; canonical implementation: atlaspm.execution.close_drafts."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.close_drafts is deprecated; use atlaspm.execution.close_drafts", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.execution.close_drafts")
