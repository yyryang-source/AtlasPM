"""Backward-compatible module path; canonical implementation: atlaspm.execution.ibkr_paper_orders."""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.ibkr_paper_orders is deprecated; use atlaspm.execution.ibkr_paper_orders", DeprecationWarning, stacklevel=2)

if __name__ == "__main__":
    raise SystemExit(_import_module("atlaspm.execution.ibkr_paper_orders").main())

_modules[__name__] = _import_module("atlaspm.execution.ibkr_paper_orders")
