"""Transparent inverse-volatility baseline (not trained AI)."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite


def inverse_volatility_weights(
    annualized_volatility: Mapping[str, float],
    max_weight: float = 0.60,
) -> dict[str, float]:
    """Return positive, fully invested weights, subject to an upper cap.

    This heuristic ignores correlations, expected returns, transaction costs,
    concentration across correlated holdings, and margin requirements.
    """
    if not annualized_volatility:
        raise ValueError("Provide at least one symbol")
    if not isinstance(max_weight, (int, float)) or isinstance(max_weight, bool):
        raise ValueError("max_weight must be numeric")
    if not isfinite(max_weight) or not (0 < max_weight <= 1):
        raise ValueError("max_weight must be in (0, 1]")
    n = len(annualized_volatility)
    if n * max_weight < 1 - 1e-12:
        raise ValueError("max_weight is too small to allocate 100%")

    inverse: dict[str, float] = {}
    for symbol, vol in annualized_volatility.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("Symbols must be nonempty strings")
        if isinstance(vol, bool) or not isinstance(vol, (int, float)):
            raise ValueError(f"Invalid volatility for {symbol}")
        if not isfinite(vol) or vol <= 0:
            raise ValueError(f"Volatility must be positive for {symbol}")
        inverse[symbol] = 1.0 / vol

    weights = {symbol: 0.0 for symbol in inverse}
    remaining = set(inverse)
    unallocated = 1.0

    while remaining:
        inv_sum = sum(inverse[symbol] for symbol in remaining)
        proposed = {
            symbol: unallocated * inverse[symbol] / inv_sum
            for symbol in remaining
        }
        capped = [s for s in remaining if proposed[s] > max_weight + 1e-12]
        if not capped:
            for symbol in remaining:
                weights[symbol] = proposed[symbol]
            break
        for symbol in capped:
            weights[symbol] = float(max_weight)
            unallocated -= max_weight
            remaining.remove(symbol)

    return dict(sorted(weights.items()))
