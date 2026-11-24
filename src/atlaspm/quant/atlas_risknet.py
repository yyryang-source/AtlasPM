"""Deprecated import path; canonical source: atlaspm.risk.atlas_risknet.

Retained as a module-object alias for old callers and patch targets.
"""

from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.quant.atlas_risknet is deprecated; use atlaspm.risk.atlas_risknet", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.risk.atlas_risknet")
