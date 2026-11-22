"""Historical expected shortfall for a long-only risky asset sleeve.

This is a past-only empirical tail estimator, not a forecast or an optimizer.
"""

from __future__ import annotations

from math import ceil, isfinite
from typing import Sequence


def historical_daily_expected_shortfall(
    daily_returns: Sequence[Sequence[float]],
    weights: Sequence[float],
    *,
    tail_fraction: float = 0.05,
) -> dict[str, float | int]:
    """Average daily loss of the worst ceil(alpha*N) past sleeve returns.

    Reports a nonnegative loss fraction; losses are 0 when even worst
    observations are profitable. Assumes perfectly aligned asset returns.
    """
    if isinstance(tail_fraction, bool) or not isinstance(tail_fraction, (int, float)):
        raise ValueError("tail_fraction must be numeric")
    alpha = float(tail_fraction)
    if not isfinite(alpha) or not (0 < alpha <= 0.25):
        raise ValueError("tail_fraction must be between 0 and 0.25, exclusive of zero")
    if not weights:
        raise ValueError("At least one weight is required")
    w = list(weights)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not isfinite(value) or value < 0 for value in w):
        raise ValueError("Weights must be finite and nonnegative")
    if abs(sum(w) - 1) > 1e-8:
        raise ValueError("Weights must sum to one")
    if len(daily_returns) < 20:
        raise ValueError("At least 20 historical daily observations are required")

    losses: list[float] = []
    for row in daily_returns:
        if len(row) != len(w):
            raise ValueError("Historical daily return row width differs from weights")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not isfinite(value) or value < -1.0 for value in row):
            raise ValueError("Historical daily returns must be finite and >= -1")
        losses.append(-sum(weight * asset_return for weight, asset_return in zip(w, row)))
    k = max(1, ceil(len(losses) * alpha))
    worst = sorted(losses, reverse=True)[:k]
    es = max(0.0, sum(worst) / k)
    return {
        "tail_fraction": alpha,
        "historical_days": len(losses),
        "tail_days": k,
        "daily_expected_shortfall_fraction": es,
        "worst_observed_daily_loss": max(0.0, worst[0]),
    }
