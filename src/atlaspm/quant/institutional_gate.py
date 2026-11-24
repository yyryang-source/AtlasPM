"""Deprecated import path; canonical source: atlaspm.execution.institutional_gate.

Retained as a module-object alias for old callers and patch targets.
"""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.quant.institutional_gate is deprecated; use atlaspm.execution.institutional_gate", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.execution.institutional_gate")
