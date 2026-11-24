"""Independent synthetic margin stress path, not an executable margin account."""
from datetime import date
from atlaspm.engine.contracts import finite


def margin_stress(sessions, *, initial_equity, leverage, annual_rate, maintenance_fraction):
    """Fixed-share equivalent risky sleeve; no new borrowing after inception.

    Interest accrues on calendar days, ACT/365, capitalized into debt.
    Breaches are observations, not broker liquidation predictions.
    """
    equity = finite(initial_equity,"initial_equity",minimum=.01)
    lev = finite(leverage,"leverage",minimum=1)
    rate = finite(annual_rate,"annual_rate",minimum=0)
    maint = finite(maintenance_fraction,"maintenance_fraction",minimum=.000001)
    if lev>4 or rate>1 or maint>=1 or len(sessions)<2:
        raise ValueError("INVALID_MARGIN_STRESS_CONFIG")
    asset = equity*lev
    debt = equity*(lev-1)
    previous = None
    path = []
    cumulative = 0.
    for row in sessions:
        day = date.fromisoformat(row["date"])
        mark = finite(row["synthetic_index"],"synthetic_index",minimum=.000001)
        if row.get("source")!="synthetic":
            raise ValueError("UNVERIFIED_MARGIN_STRESS_SOURCE")
        interest = 0.
        if previous:
            days=(day-previous[0]).days
            if days<=0:
                raise ValueError("INVALID_MARGIN_STRESS_DATE_ORDER")
            asset *= mark/previous[1]
            interest=debt*rate*days/365
            debt += interest
            cumulative += interest
        nlv=asset-debt
        requirement=asset*maint if debt else 0.
        excess=nlv-requirement
        path.append(dict(date=str(day),market_value=asset,debt=debt,net_liquidation=nlv,
                         financing_interest=interest,maintenance_requirement=requirement,
                         maintenance_excess=excess,breach=excess<0,insolvent=nlv<=0))
        previous=(day,mark)
        if nlv<=0:
            break
    return dict(research_only=True,live_execution_enabled=False,broker_order_submission_enabled=False,
                approved_for_model_training=False,broker_margin_verified=False,
                classification="SYNTHETIC_FIXED_EXPOSURE_MARGIN_STRESS_NOT_BROKER_LIQUIDATION",
                path=path,total_interest=cumulative,
                first_breach=next((p["date"] for p in path if p["breach"]),None),
                terminated_insolvent=path[-1]["insolvent"])
