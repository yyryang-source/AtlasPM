"""Compatibility import; canonical implementation: atlaspm.execution.atlas_execution_policy."""
from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.atlas_execution_policy is deprecated; use atlaspm.execution.atlas_execution_policy", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.execution.atlas_execution_policy")
