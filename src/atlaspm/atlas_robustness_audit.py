"""Backward-compatible module path; canonical implementation: atlaspm.research.atlas_robustness_audit."""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("atlaspm.research.atlas_robustness_audit")
