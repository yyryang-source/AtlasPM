"""Hypothetical leverage envelope; not a broker margin model or order permit."""
from __future__ import annotations

from atlaspm.engine.contracts import finite
from atlaspm.risk.ibkr_reverse_stress import _boundary


def envelope(equity,*,scenarios,max_gross=4.,max_annual_interest_fraction=.15):
    """Solve a conservative static long-equity margin envelope.

    All positions move by the same assumed shock. Borrowing equals gross
    notional less starting equity; no other assets, collateral or cash.
    Margin maintenance is an assumed fraction of stressed gross value.
    No funding, actual trade, or verified liquidation price is implied.
    """
    e=finite(equity,"equity",minimum=.01)
    cap=finite(max_gross,"max_gross",minimum=1.)
    interest_cap=finite(max_annual_interest_fraction,"max_annual_interest_fraction",minimum=0.)
    if cap>4 or interest_cap>1 or not isinstance(scenarios,list) or not 1<=len(scenarios)<=32:
        raise ValueError("INVALID_LEVERAGE_ENVELOPE_CONFIG")
    results=[]
    for s in scenarios:
        if not isinstance(s,dict) or set(s)!={"shock","maintenance_fraction","initial_fraction","apr","days","min_excess_fraction","max_equity_loss"}:
            raise ValueError("INVALID_LEVERAGE_SCENARIO")
        d=finite(s["shock"],"shock",minimum=0)
        m=finite(s["maintenance_fraction"],"maintenance_fraction",minimum=.000001)
        initial=finite(s["initial_fraction"],"initial_fraction",minimum=m)
        apr=finite(s["apr"],"apr",minimum=0)
        b=finite(s["min_excess_fraction"],"min_excess_fraction",minimum=0)
        loss=finite(s["max_equity_loss"],"max_equity_loss",minimum=0)
        days=s["days"]
        if not d<1 or initial>1 or apr>1 or b>=1 or not 0<loss<1 or type(days) is not int or not 0<=days<=365:
            raise ValueError("INVALID_LEVERAGE_SCENARIO_LIMIT")
        r=apr*days/365
        limits={"configured_gross_cap":cap,"assumed_initial_margin":1/initial,
                "stressed_maintenance_buffer":(1-b)*(1+r)/((1-b)*(d+r)+m*(1-d)),
                "equity_loss_budget":(loss+r)/(d+r) if d+r else cap,
                "annual_interest_budget":1+interest_cap/apr if apr else cap}
        raw=min(limits.values())
        # A value below one means the fully invested case itself fails.
        # Do not clamp up to 1 or treat it as an approved borrowing amount.
        gross=raw if raw>=1 else None
        detail={"assumptions":s,"limits":limits,"binding_constraints":[k for k,v in limits.items() if abs(v-raw)<1e-9],
                "gross_leverage_ceiling_if_fully_invested":gross,
                "status":"ILLUSTRATIVE_ENVELOPE" if gross is not None else "FULLY_INVESTED_CASE_FAILS"}
        if gross is not None:
            notional=e*gross
            debt=notional-e
            interest=debt*r
            stressed_equity=e-notional*d-interest
            maint=notional*(1-d)*m
            excess=stressed_equity-maint
            # Reuse the existing static reverse-boundary helper. Maintenance
            # declines with market value in this particular explicit scenario.
            detail.update(assumed_gross_notional=notional,assumed_debt=debt,
                assumed_horizon_interest=interest,stressed_equity=stressed_equity,
                stressed_maintenance=maint,stressed_excess=excess,
                equity_loss_fraction=(e-stressed_equity)/e,
                static_maintenance_zero_excess_decline=_boundary(e-interest-notional*m,notional*(1-m),1.))
        results.append(detail)
    ceilings=[x["gross_leverage_ceiling_if_fully_invested"] for x in results]
    return {"status":"HYPOTHETICAL_LEVERAGE_REVIEW_ONLY","starting_equity_assumption":e,
            "scenarios":results,"worst_case_gross_ceiling":None if any(x is None for x in ceilings) else min(ceilings),
            "broker_margin_verified":False,"broker_order_submission_enabled":False,
            "approved_for_model_training":False,"live_execution_enabled":False,
            "warning":"Static hypothetical envelope, not a broker limit, trading recommendation or actual financing authorization."}
