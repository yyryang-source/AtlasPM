"""Lagged, one-session, long-only synthetic strategy comparison.

A close-observed forecast on t is applied at close t+1 and held to t+2.
No missing outcome is silently converted into cash or a surviving universe.
This is a research proxy, not an OMS fill or capacity estimate.
"""
from __future__ import annotations

from itertools import groupby
from math import sqrt, prod, isfinite
from random import Random
from statistics import fmean, stdev
from atlaspm.research.training_engine import _metrics, _correlation, _ranks
from atlaspm.engine.contracts import finite


def evaluate(rows, sessions, *, top_fraction=.25, cost_bps=10.):
    fraction=finite(top_fraction,"top_fraction",minimum=.000001)
    cost=finite(cost_bps,"cost_bps",minimum=0)/10000
    if fraction>1 or cost>=1:
        raise ValueError("INVALID_STRATEGY_COST_OR_FRACTION")
    dates=[s["date"] for s in sessions]
    index={d:i for i,d in enumerate(dates)}
    if len(index)!=len(dates) or dates!=sorted(dates):
        raise ValueError("INVALID_STRATEGY_CALENDAR")
    # Keep only an index to original sessions; materialize at most two mark maps.
    daily=[]
    previous={}
    previous_signal=None
    last_equity=1.
    peak=1.
    max_dd=0.
    gross=[]
    for day, group in groupby(rows,key=lambda r:r["date"]):
        i=index[day]
        if previous_signal is not None and i!=previous_signal+1:
            raise ValueError("NONCONTIGUOUS_STRATEGY_SIGNALS")
        previous_signal=i
        bundle=list(group)
        if i+2>=len(sessions):
            continue
        names=[r["security_id"] for r in bundle]
        if not names or len(set(names))!=len(names):
            raise ValueError("INVALID_STRATEGY_UNIVERSE")
        for r in bundle:
            if r["label_end_date"]!=dates[i+1]:
                raise ValueError("STRATEGY_REQUIRES_ONE_SESSION_LABELS")
            finite(r["prediction"],"prediction")
        n=max(1,int(len(bundle)*fraction))
        # Include all ties at the cutoff; no arbitrary ticker advantage.
        cutoff=sorted((r["prediction"] for r in bundle),reverse=True)[n-1]
        chosen=sorted(r["security_id"] for r in bundle if r["prediction"]>=cutoff)
        target={s:1/len(chosen) for s in chosen}
        entry={r["security_id"]:r["close"] for r in sessions[i+1]["assets"]}
        exit_={r["security_id"]:r["close"] for r in sessions[i+2]["assets"]}
        # Require complete outcomes for the entire eligible universe, not just picks.
        if set(names)-entry.keys() or set(names)-exit_.keys():
            raise ValueError("MISSING_EXECUTABLE_PATH")
        turnover=sum(abs(target.get(s,0.)-previous.get(s,0.)) for s in target.keys()|previous.keys())
        returns={s:finite(exit_[s],"exit",minimum=.000001)/
                   finite(entry[s],"entry",minimum=.000001)-1 for s in target}
        r=sum(target[s]*returns[s] for s in target)
        # Include entry costs, subsequent drift-aware rebalances and final liquidation.
        net=(1-turnover*cost)*(1+r)-1
        if net<=-1:
            raise ValueError("STRATEGY_INSOLVENT")
        previous={s:target[s]*(1+returns[s])/(1+r) for s in target}
        daily.append(dict(signal_date=day,entry_date=dates[i+1],exit_date=dates[i+2],
                          gross=r,net=net,turnover=turnover,selected=chosen))
    if not daily:
        raise ValueError("NO_EXECUTABLE_RESEARCH_PERIODS")
    daily[-1]["net"]=(1+daily[-1]["net"])*(1-cost)-1
    daily[-1]["turnover"]+=1.
    for row in daily:
        last_equity*=1+row["net"]
        peak=max(peak,last_equity)
        max_dd=min(max_dd,last_equity/peak-1)
        gross.append(row["gross"])
    net=[r["net"] for r in daily]
    sd=stdev(net) if len(net)>1 else 0.
    downside=sqrt(fmean(min(0.,r)**2 for r in net))
    cagr=last_equity**(252/len(net))-1
    ric=[]
    for _,group in groupby(rows,key=lambda r:r["date"]):
        valid=[r for r in group if r["actual"] is not None]
        if valid:
            value=_correlation(_ranks([r["prediction"] for r in valid]),
                               _ranks([r["actual"] for r in valid]))
            if value is not None:
                ric.append(value)
    ic_sd=stdev(ric) if len(ric)>1 else 0.
    metrics=_metrics(rows)
    metrics.update(icir=fmean(ric)/ic_sd if ic_sd>1e-12 else None,
                   periods=len(net),net_return=last_equity-1,gross_return=prod(1+r for r in gross)-1,
                   cagr=cagr,sharpe=fmean(net)/sd*sqrt(252) if sd>1e-12 else None,
                   sortino=fmean(net)/downside*sqrt(252) if downside>1e-12 else None,
                   max_drawdown=max_dd,calmar=cagr/abs(max_dd) if max_dd else None,
                   mean_turnover=fmean(r["turnover"] for r in daily),
                   capacity_verified=False,annualization_sessions=252)
    if any(isinstance(v,float) and not isfinite(v) for v in metrics.values()):
        raise ValueError("NONFINITE_STRATEGY_METRIC")
    return dict(metrics=metrics,daily=daily)


def paired_lower(candidate, baseline, *, seed, draws=1000, block=5, tail=.05):
    """Circular moving-block bootstrap on paired daily net excess returns."""
    if type(draws) is not int or not 100<=draws<=10000 or type(block) is not int or block<1 or not 0<tail<.5:
        raise ValueError("INVALID_BOOTSTRAP_SETTINGS")
    a,b=candidate["daily"],baseline["daily"]
    keys=lambda rows:[(r["entry_date"],r["exit_date"]) for r in rows]
    if keys(a)!=keys(b) or len(a)<max(2*block,10):
        raise ValueError("INSUFFICIENT_OR_UNALIGNED_BOOTSTRAP_PERIODS")
    values=[x["net"]-y["net"] for x,y in zip(a,b)]
    n=len(values)
    rng=Random(seed)
    means=[]
    for _ in range(draws):
        sample=[]
        while len(sample)<n:
            start=rng.randrange(n)
            sample.extend(values[(start+j)%n] for j in range(min(block,n-len(sample))))
        means.append(fmean(sample))
    means.sort()
    return dict(mean_excess=fmean(values),lower_bound=means[int(tail*draws)],
                draws=draws,block=block,one_sided_tail=tail)
