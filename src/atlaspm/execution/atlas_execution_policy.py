"""Own cost-aware no-trade policy for an AtlasPM CASH-ONLY historical ledger.

A prediction can request fewer trades but can NEVER bypass the independently
computed risk bounds. No live broker execution or margin financing.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from atlaspm.research.ml_dynamic_rebalance import _select_allocation


def assess_native_no_trade(
    *,
    symbols: list[str],
    existing_holdings: list[float],
    existing_cash: float,
    equity: float,
    proposed_allocation: dict[str, Any],
    risk_forecasts: list[float],
    trailing_daily_returns: list[list[float]],
    no_trade_band: float,
    name_weight_cap: float,
    risk_policy: dict[str, float],
    account_drawdown: float,
    additional_exposure_cap: float = 1.0,
) -> dict[str, Any]:
    """Use L1 account-weight gap, then re-run risk guard with ACTUAL holdings.

    No trade if the portfolio's current weight drift is small *and* its actual
    risky sleeve, expected future risk, historical ES, stress and drawdown are
    still inside the same hard caps. Initial purchase is never silently skipped.
    The already-paid prior trade fees are not recharged for a skipped order.
    """
    if not (isinstance(no_trade_band, (int, float))
            and not isinstance(no_trade_band, bool) and isfinite(no_trade_band)
            and 0 <= no_trade_band <= .5):
        raise ValueError("no_trade_band must be between zero and 0.5")
    if (not isfinite(equity) or equity <= 0
            or len(existing_holdings) != len(symbols)
            or len(risk_forecasts) != len(symbols)
            or not isfinite(existing_cash) or existing_cash < -1e-10
            or any(not isfinite(h) or h < -1e-10 for h in existing_holdings)):
        raise ValueError("Invalid pre-trade cash-only state")
    if abs(sum(existing_holdings)+existing_cash-equity) > 1e-8*max(1.0, equity):
        raise ValueError("Pre-trade cash holdings do not reconcile")
    if set(proposed_allocation["account_weight_targets"]) != set(symbols):
        raise ValueError("Proposed allocation symbols differ")
    proposed = [
        proposed_allocation["account_weight_targets"][name]
        for name in symbols
    ]
    actual = [h/equity for h in existing_holdings]
    current_gross = sum(actual)
    proposed_cash_weight = proposed_allocation["cash_weight"]
    current_cash_weight = existing_cash/equity
    gap = sum(abs(a-b) for a,b in zip(actual, proposed)) + abs(
        current_cash_weight-proposed_cash_weight
    )
    if not isfinite(gap) or gap < 0:
        raise ValueError("Invalid proposed turnover gap")
    result = {
        "skipped": False,
        "reason": "REBALANCE_REQUESTED",
        "account_weight_l1_gap": gap,
        "existing_risky_gross": current_gross,
        "proposal_risky_gross": proposed_allocation["risky_gross"],
        "risk_eligibility_rechecked": False,
    }
    if no_trade_band == 0:
        result["reason"] = "NO_TRADE_BAND_DISABLED"
        return result
    if current_gross <= 1e-12:
        result["reason"] = "INITIAL_ENTRY_MUST_TRADE"
        return result
    if gap > no_trade_band + 1e-12:
        result["reason"] = "WEIGHT_GAP_EXCEEDS_NO_TRADE_BAND"
        return result
    current_sleeve = {
        name: weight/current_gross for name,weight in zip(symbols, actual)
    }
    if any(weight > name_weight_cap + 1e-10 for weight in current_sleeve.values()):
        result["reason"] = "DRIFTED_CONCENTRATION_EXCEEDS_CAP"
        return result
    # Critical: use *current* risk sleeve, not the newly proposed one.
    # A no-trade suggestion cannot hide an exposure violation by referring
    # to a proposed diversified basket that has not actually been traded.
    actual_allowed = _select_allocation(
        symbols, risk_forecasts, trailing_daily_returns,
        maximum_name_weight=name_weight_cap,
        target_annual_volatility=risk_policy["target_annual_volatility"],
        maximum_gross_exposure=risk_policy["maximum_gross_exposure"],
        uniform_stress_decline=risk_policy["uniform_stress_decline"],
        maximum_uniform_stress_loss=risk_policy["maximum_uniform_stress_loss"],
        maximum_daily_tail_loss=risk_policy["maximum_daily_tail_loss"],
        account_drawdown=account_drawdown,
        account_drawdown_trigger=risk_policy["account_drawdown_trigger"],
        drawdown_exposure_cap=risk_policy["drawdown_exposure_cap"],
        sleeve_weights=current_sleeve,
        additional_exposure_cap=additional_exposure_cap,
    )
    result["risk_eligibility_rechecked"] = True
    result["current_sleeve_max_permitted_gross"] = actual_allowed["risky_gross"]
    if current_gross > actual_allowed["risky_gross"] + 1e-9:
        result["reason"] = "CURRENT_HOLDINGS_BREACH_UPDATED_RISK_BUDGET"
        return result
    result["reason"] = "NO_TRADE_BAND_AND_RISK_CHECK_PASSED"
    result["skipped"] = True
    return result
