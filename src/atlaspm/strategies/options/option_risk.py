"""European option quote IV/Greeks diagnostics on explicit scenario inputs.

No American approximation, inferred dividend/rate, executable pricing,
broker margin, or certified data. Quote history is read-only.
"""
from __future__ import annotations

from datetime import timedelta
from math import erfc, exp, log, pi, sqrt
from atlaspm.engine.contracts import finite
from atlaspm.data.option_history import option_timestamp
from atlaspm.research.training_registry import FLAGS


def _normal(x):
    return .5*erfc(-x/sqrt(2.))


def european(right,spot,strike,years,rate,dividend,volatility):
    if right not in ("call","put"):
        raise ValueError("OPTION_RISK_RIGHT")
    s=finite(spot,"spot",minimum=.000001)
    k=finite(strike,"strike",minimum=.000001)
    t=finite(years,"years",minimum=1/365/24/60)
    r=finite(rate,"rate")
    q=finite(dividend,"dividend",minimum=0)
    v=finite(volatility,"volatility",minimum=.00001)
    if t>10 or abs(r)>1 or q>1 or v>5 or max(s,k)>1e9:
        raise ValueError("OPTION_RISK_SCENARIO_BOUNDS")
    root=sqrt(t)
    ds=exp(-q*t)
    dk=exp(-r*t)
    d1=(log(s/k)+(r-q+.5*v*v)*t)/(v*root)
    d2=d1-v*root
    pdf=exp(-.5*d1*d1)/sqrt(2*pi)
    sign=1 if right=="call" else -1
    price=max(0.,sign*(s*ds*_normal(sign*d1)-k*dk*_normal(sign*d2)))
    delta=sign*ds*_normal(sign*d1)
    gamma=ds*pdf/(s*v*root)
    vega=s*ds*pdf*root/100
    theta=(-s*ds*pdf*v/(2*root)-sign*r*k*dk*_normal(sign*d2)
           +sign*q*s*ds*_normal(sign*d1))/365
    rho=sign*k*t*dk*_normal(sign*d2)/100
    return {"price_per_share":price,"delta":delta,"gamma":gamma,
            "vega_per_vol_point":vega,"theta_per_calendar_day":theta,"rho_per_rate_point":rho}


def implied_volatility(right,price,spot,strike,years,rate,dividend):
    price=finite(price,"quote_price",minimum=0)
    args=(right,spot,strike,years,rate,dividend)
    lo,hi=.00001,5.
    low=european(*args,lo)["price_per_share"]
    high=european(*args,hi)["price_per_share"]
    # No IV identified on a boundary or in a numerically flat price region.
    tol=max(1e-10,1e-10*max(spot,strike))
    if not low+tol<price<high-tol:
        return None
    for _ in range(64):
        mid=(lo+hi)/2
        if european(*args,mid)["price_per_share"]<price:
            lo=mid
        else:
            hi=mid
    result=(lo+hi)/2
    if abs(european(*args,result)["price_per_share"]-price)>tol:
        return None
    return result


def review_chain(history,contract_ids,*,as_of,spot,spot_available_at,rate,dividend,
                 max_age_seconds=300,spot_shock=-.1,volatility_shock=.1):
    cutoff=option_timestamp(as_of)
    available=option_timestamp(spot_available_at)
    if type(max_age_seconds) is not int or not 1<=max_age_seconds<=3600:
        raise ValueError("OPTION_RISK_QUOTE_AGE")
    if available>cutoff or (cutoff-available).total_seconds()>max_age_seconds:
        raise ValueError("OPTION_RISK_SPOT_LOOKAHEAD_OR_STALE")
    if (not isinstance(contract_ids,(list,tuple)) or not 1<=len(contract_ids)<=128
            or any(not isinstance(k,str) or not k for k in contract_ids)
            or len(set(contract_ids))!=len(contract_ids)):
        raise ValueError("OPTION_RISK_CONTRACT_BUDGET")
    # One explicit common underlying spot, not different symbols using a
    # single mark. Rates/dividend are declared scenarios, not market facts.
    for k in contract_ids:
        history._require_contract(k)
    underlying={history.contracts[k].underlying for k in contract_ids}
    if len(underlying)!=1:
        raise ValueError("OPTION_RISK_COMMON_UNDERLYING_REQUIRED")
    spot=finite(spot,"spot",minimum=.000001)
    rate=finite(rate,"rate")
    dividend=finite(dividend,"dividend",minimum=0)
    ss=finite(spot_shock,"spot_shock")
    vs=finite(volatility_shock,"volatility_shock")
    if not -.95<=ss<=2 or not -1<=vs<=2 or abs(rate)>1 or dividend>1 or spot>1e9:
        raise ValueError("OPTION_RISK_SCENARIO_BOUNDS")
    rows=[]
    for key in sorted(contract_ids):
        c=history.contracts[key]
        row={"contract_id":key,"right":c.right,"strike":c.strike,
             "expiration_at":c.expiration_at.isoformat(),"multiplier":c.multiplier}
        years=(c.expiration_at-cutoff).total_seconds()/(365*86400)
        if c.exercise_style!="european":
            row["status"]="AMERICAN_STYLE_NOT_MODELED"
        elif cutoff>c.last_trade_at or not 1/365/24/60<=years<=10:
            row["status"]="OUTSIDE_MODELED_TRADABLE_WINDOW"
        else:
            try:
                quote=history.quote_at(key,cutoff,timedelta(seconds=max_age_seconds))
            except ValueError:
                row["status"]="FRESH_QUOTE_REQUIRED"
            else:
                row.update(quote_at=quote.at.isoformat(),bid=quote.bid,ask=quote.ask)
                if quote.bid<=0 or not quote.bid_size or not quote.ask_size:
                    row["status"]="TWO_SIDED_QUOTE_REQUIRED"
                else:
                    args=(spot,c.strike,years,rate,dividend)
                    ivs={name:implied_volatility(c.right,value,*args) for name,value in
                         (("bid",quote.bid),("mid",(quote.bid+quote.ask)/2),("ask",quote.ask))}
                    row["implied_volatility"]=ivs
                    if any(v is None for v in ivs.values()):
                        row["status"]="IV_NOT_IDENTIFIED_IN_MODEL_BOUNDS"
                    else:
                        vol=ivs["mid"]
                        model=european(c.right,*args,vol)
                        row["greeks_per_share"]=model
                        row["greeks_per_contract"]={k:c.multiplier*v for k,v in model.items() if k!="price_per_share"}
                        shocked_vol=vol+vs
                        if not .00001<=shocked_vol<=5 or not .000001<=spot*(1+ss)<=1e9:
                            row["status"]="STRESS_OUTSIDE_MODEL_BOUNDS"
                        else:
                            shocked=european(c.right,spot*(1+ss),c.strike,years,rate,dividend,shocked_vol)
                            row["scenario_pnl_per_long_contract"]=(shocked["price_per_share"]-
                                                                    model["price_per_share"])*c.multiplier
                            row["status"]="EUROPEAN_MODEL_DIAGNOSTIC"
        rows.append(row)
    return {"schema":"atlaspm_european_option_risk_v1",**FLAGS,
            "underlying":next(iter(underlying)),"as_of":cutoff.isoformat(),
            "declared_spot":spot,"spot_available_at":available.isoformat(),
            "declared_continuous_rate":rate,"declared_continuous_dividend_yield":dividend,
            "spot_shock":ss,"volatility_absolute_shock":vs,"contracts":rows,
            "all_contracts_modeled":all(r["status"]=="EUROPEAN_MODEL_DIAGNOSTIC" for r in rows),
            "approved_for_backtest":False,"quote_basis_verified":False,
            "warning":"European Black-Scholes-Merton scenarios. No smile fit, early exercise, observed rate/dividend verification or executable prices."}
