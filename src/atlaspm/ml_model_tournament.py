"""Backward-compatible module path; canonical implementation: atlaspm.research.ml_model_tournament."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.ml_model_tournament is deprecated; use atlaspm.research.ml_model_tournament", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.research.ml_model_tournament")
