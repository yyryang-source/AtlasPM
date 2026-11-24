"""Backward-compatible module path; canonical implementation: atlaspm.data.us_market_universe."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.us_market_universe is deprecated; use atlaspm.data.us_market_universe", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.data.us_market_universe")
