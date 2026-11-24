"""Backward-compatible module path; canonical implementation: atlaspm.strategies.equity.monthly_stock_selection."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.monthly_stock_selection is deprecated; use atlaspm.strategies.equity.monthly_stock_selection", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.strategies.equity.monthly_stock_selection")
