"""Quote-costed terminal payoff diagnostics for covered European hedges.

No order generation, short-option execution, margin model, early assignment,
or guarantee that stock liquidation matches the option settlement reference.
All arms use the same declared existing stock holding and settled cash.
"""
from __future__ import annotations

from datetime import timedelta
from math import fsum
from atlaspm.data.option_history import option_timestamp
from atlaspm.engine.contracts import finite
from atlaspm.research.training_registry import FLAGS, digest


def compare_expiry_hedges(history, *, as_of, spot, spot_available_at, shares,
                          settled_cash, minimum_cash, long_put, lower_put,
                          covered_call, terminal_spots, fee_per_contract=1.,
                          max_age_seconds=300):
    """Compare unhedged, protective put, vertical put spread and collar.

    Explicit selected contracts only, same underlying, expiry, last trade,
    European cash settlement and multiplier. Short legs are payoff
    assumptions only, not admitted positions. No premium receipts used to
    fund long legs; hypothetical received premiums enter terminal NAV.
    """
    cutoff=option_timestamp(as_of)
    available=option_timestamp(spot_available_at)
    if (type(max_age_seconds) is not int or not 1<=max_age_seconds<=3600
            or not available<=cutoff or (cutoff-available).total_seconds()>max_age_seconds):
        raise ValueError("HEDGE_SPOT_LOOKAHEAD_OR_STALE")
    spot=finite(spot,"spot",minimum=.000001)
    cash=finite(settled_cash,"settled_cash",minimum=0)
    reserve=finite(minimum_cash,"minimum_cash",minimum=0)
    fee=finite(fee_per_contract,"fee",minimum=0)
    if (type(shares) is not int or not 1<=shares<=1000000 or cash<reserve
            or spot>1e9 or cash>1e12 or fee>10000):
        raise ValueError("HEDGE_SCENARIO_BUDGET")
    keys=(long_put,lower_put,covered_call)
    if any(not isinstance(k,str) or not k for k in keys) or len(set(keys))!=3:
        raise ValueError("HEDGE_DISTINCT_CONTRACTS_REQUIRED")
    if not isinstance(terminal_spots,(list,tuple)) or not 3<=len(terminal_spots)<=100:
        raise ValueError("HEDGE_SCENARIO_COUNT")
    spots=[finite(s,"terminal_spot",minimum=0) for s in terminal_spots]
    if (spots!=sorted(set(spots)) or spots[0]>=spot or spots[-1]<=spot
            or spots[-1]>10*spot):
        raise ValueError("HEDGE_ORDERED_DOWNSIDE_AND_UPSIDE_SCENARIOS_REQUIRED")
    for k in keys:
        history._require_contract(k)
    contracts=[history.contracts[k] for k in keys]
    hi,lo,call=contracts
    if (any(c.exercise_style!="european" or c.settlement_type!="cash"
            or c.expiration_at!=hi.expiration_at or c.last_trade_at!=hi.last_trade_at
            or c.underlying!=hi.underlying or c.multiplier!=hi.multiplier
            or c.exercise_threshold_usd!=0 for c in contracts)
            or not cutoff<=hi.last_trade_at<hi.expiration_at
            or not lo.strike<hi.strike<=spot<=call.strike
            or (hi.right,lo.right,call.right)!=("put","put","call")
            or shares%hi.multiplier):
        raise ValueError("HEDGE_INCOMPATIBLE_EUROPEAN_CONTRACTS")
    count=shares//hi.multiplier
    quotes={}
    for k in keys:
        q=history.quote_at(k,cutoff,timedelta(seconds=max_age_seconds))
        if q.bid<=0 or min(q.bid_size,q.ask_size)<count:
            raise ValueError("HEDGE_TWO_SIDED_LIQUIDITY_REQUIRED")
        quotes[k]={"bid":q.bid,"ask":q.ask,"at":q.at.isoformat(),
                   "bid_size":q.bid_size,"ask_size":q.ask_size}
    if len({q["at"] for q in quotes.values()})!=1:
        raise ValueError("HEDGE_SYNCHRONIZED_QUOTE_REQUIRED")
    initial=spot*shares+cash
    arms={"unhedged":(),"protective_put":((long_put,1),),
          "put_spread":((long_put,1),(lower_put,-1)),
          "collar":((long_put,1),(covered_call,-1))}
    # A short option's credit is never assumed to be settled or available
    # to fund purchase or fees. This is deliberately conservative funding.
    output={}
    for name,legs in arms.items():
        debit=fsum(quotes[k]["ask"]*shares for k,side in legs if side==1)
        credit=fsum(quotes[k]["bid"]*shares for k,side in legs if side==-1)
        fees=len(legs)*fee*count
        if debit+fees>cash-reserve:
            output[name]={"status":"INSUFFICIENT_SETTLED_CASH","gross_premium_debit":debit,
                          "assumed_premium_credit":credit,"entry_fees":fees,"scenarios":[]}
            continue
        values=[]
        for s in spots:
            payoff=0.
            for k,side in legs:
                c=history.contracts[k]
                intrinsic=max(0.,c.strike-s) if c.right=="put" else max(0.,s-c.strike)
                payoff+=side*shares*intrinsic
            nav=cash-debit+credit-fees+shares*s+payoff
            values.append({"terminal_reference":s,"option_payoff":payoff,
                           "terminal_nav":nav,"pnl":nav-initial,"return":nav/initial-1})
        output[name]={"status":"TERMINAL_PAYOFF_DIAGNOSTIC","gross_premium_debit":debit,
                      "assumed_premium_credit":credit,"entry_fees":fees,
                      "cash_after_debits_before_credit":cash-debit-fees,
                      "worst_supplied_scenario_pnl":min(v["pnl"] for v in values),
                      "best_supplied_scenario_pnl":max(v["pnl"] for v in values),
                      "scenarios":values}
    return {"schema":"atlaspm_covered_european_hedge_scenarios_v1",**FLAGS,
            "approved_for_backtest":False,"underlying":hi.underlying,
            "as_of":cutoff.isoformat(),"expiry":hi.expiration_at.isoformat(),
            "shares":shares,"initial_marked_nav":initial,"minimum_cash":reserve,
            "contracts":dict(zip(("long_put","lower_put","covered_call"),keys)),
            "quotes":quotes,"arms":output,"scenario_sha256":digest(spots),
            "short_option_execution_supported":False,"broker_margin_verified":False,
            "winner_selected":False,"terminal_spot_is_declared_scenario":True,
            "warning":"Terminal payoff only; assumes same reference for stock liquidation and cash settlement, excludes dividends, interest, closing costs and timing/assignment risk. No actual fills, executable hedge, or probability estimate."}


def main():
    """Bounded JSON parameter file and existing normalized quote CSVs."""
    import argparse
    import json
    from pathlib import Path
    from atlaspm.data.market_db import private_path
    from atlaspm.data.option_history import OptionHistory
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("contracts","quotes","settlements","plan"):
        parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args()
    path=private_path(args.plan,exists=True)
    with path.open("rb") as handle:
        raw=handle.read(65537)
    if len(raw)>65536:
        raise ValueError("HEDGE_PLAN_BYTE_BUDGET")
    from atlaspm.data.api_gateway import _strict_json
    plan=_strict_json(raw)
    history=OptionHistory(*(private_path(p,exists=True)
                             for p in (args.contracts,args.quotes,args.settlements)))
    print(json.dumps(compare_expiry_hedges(history,**plan),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
