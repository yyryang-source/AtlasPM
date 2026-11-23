"""AtlasPM command adapters: *_cli files only expose domain algorithms.

Algorithms and models live in atlaspm.quant and atlaspm.selection.
Keep command adapters out of those packages so the file tree is unambiguous.
"""
