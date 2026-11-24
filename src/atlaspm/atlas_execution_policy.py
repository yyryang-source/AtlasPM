"""Compatibility import; canonical implementation: atlaspm.execution.atlas_execution_policy."""
from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("atlaspm.execution.atlas_execution_policy")
