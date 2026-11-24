"""Backward-compatible module path; canonical implementation: atlaspm.strategies.equity.alpha_eligibility."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.alpha_eligibility is deprecated; use atlaspm.strategies.equity.alpha_eligibility", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.strategies.equity.alpha_eligibility")
