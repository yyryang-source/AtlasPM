"""Backward-compatible module path; canonical implementation: atlaspm.execution.market_close."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.market_close is deprecated; use atlaspm.execution.market_close", DeprecationWarning, stacklevel=2)

if __name__ == "__main__":
    raise SystemExit(_import_module("atlaspm.execution.market_close").main())

_modules[__name__] = _import_module("atlaspm.execution.market_close")
