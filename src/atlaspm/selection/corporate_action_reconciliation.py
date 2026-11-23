"""Deprecated import path; canonical source: atlaspm.data.corporate_action_reconciliation.

Retained as a module-object alias for old callers and patch targets.
"""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("atlaspm.data.corporate_action_reconciliation")
