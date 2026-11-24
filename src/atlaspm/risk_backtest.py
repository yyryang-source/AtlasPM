"""Backward-compatible module path; canonical implementation: atlaspm.risk.risk_backtest."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.risk_backtest is deprecated; use atlaspm.risk.risk_backtest", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.risk.risk_backtest")
