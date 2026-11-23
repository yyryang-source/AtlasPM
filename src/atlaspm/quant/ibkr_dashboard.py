"""Deprecated import path; canonical source: atlaspm.monitoring.ibkr_dashboard.

Retained as a module-object alias for old callers and patch targets.
"""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("atlaspm.monitoring.ibkr_dashboard")
