"""Deprecated import path; canonical source: atlaspm.monitoring.audit.

Retained as a module-object alias for old callers and patch targets.
"""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.quant.audit is deprecated; use atlaspm.monitoring.audit", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.monitoring.audit")
