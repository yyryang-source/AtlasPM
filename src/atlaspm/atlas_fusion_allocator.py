"""Compatibility import; canonical implementation: atlaspm.engine.atlas_fusion_allocator."""
from importlib import import_module as _import_module
from sys import modules as _modules
from warnings import warn as _warn

_warn("atlaspm.atlas_fusion_allocator is deprecated; use atlaspm.engine.atlas_fusion_allocator", DeprecationWarning, stacklevel=2)

_modules[__name__] = _import_module("atlaspm.engine.atlas_fusion_allocator")
