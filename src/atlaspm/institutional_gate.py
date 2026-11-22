"""Backward-compatible module path; canonical implementation: atlaspm.quant.institutional_gate."""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("atlaspm.quant.institutional_gate")
