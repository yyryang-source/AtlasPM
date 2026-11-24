"""Portfolio research engine; public entry point is loaded on demand."""

__all__ = ["run_unified_portfolio"]


def __getattr__(name):
    if name == "run_unified_portfolio":
        from atlaspm.engine.unified import run_unified_portfolio
        globals()[name] = run_unified_portfolio
        return run_unified_portfolio
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
