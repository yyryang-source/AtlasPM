"""Bounded factor and economic rule candidates for synthetic panels."""
from __future__ import annotations

from itertools import groupby
from statistics import fmean, stdev

from atlaspm.research.training_engine import _correlation, _ranks

RULES = {"momentum": ("momentum", 1), "mean_reversion": ("momentum", -1),
         "trend": ("trend", 1), "low_volatility": ("volatility", -1),
         "liquidity": ("liquidity", 1)}


def factor_selection(samples, names, cutoff, *, min_dates=5, redundancy=.85):
    """Fixed training-prefix selection; V3 DuckDB path remains independent.
    All outcomes must mature strictly before the validation boundary.
    """
    ics = [[] for _ in names]
    pairs = {}
    for _, group in groupby(samples, key=lambda r:r["date"]):
        rows = [r for r in group if r["label_end_date"] < cutoff and r["y"] is not None]
        if len(rows) < 3:
            continue
        ranks = [_ranks([r["x"][i] for r in rows]) for i in range(len(names))]
        y = _ranks([r["y"] for r in rows])
        for i, x in enumerate(ranks):
            ic = _correlation(x,y)
            if ic is not None:
                ics[i].append(ic)
            for j in range(i):
                v = _correlation(x,ranks[j])
                if v is not None:
                    pairs.setdefault((j,i), []).append(abs(v))
    stats = {}
    for name, values in zip(names,ics):
        avg = fmean(values) if values else None
        sd = stdev(values) if len(values)>1 else 0.
        stats[name] = dict(dates=len(values), rank_ic=avg, icir=avg/sd if sd else None,
                           direction_stability=(fmean((v>0) if avg>0 else (v<0) for v in values)
                                                if avg else None),
                           rolling_ic=[fmean(values[max(0,i-4):i+1]) for i in range(len(values))])
    selected = []
    for i in sorted(range(len(names)),key=lambda i:(-abs(stats[names[i]]["rank_ic"] or 0),names[i])):
        s = stats[names[i]]
        if s["dates"] < min_dates or not s["rank_ic"] or s["direction_stability"] < .6:
            continue
        if any(not pairs.get(tuple(sorted((i,j)))) or
               fmean(pairs[tuple(sorted((i,j)))]) >= redundancy for j in selected):
            continue
        selected.append(i)
    total = sum(abs(stats[names[i]]["rank_ic"]) for i in selected)
    weights = {names[i]:stats[names[i]]["rank_ic"]/total for i in selected} if total else {}
    return dict(weights=weights, diagnostics=stats,
                correlations=[dict(a=names[i],b=names[j],mean_absolute_rank_correlation=fmean(v))
                              for (i,j),v in sorted(pairs.items())],
                selection_cutoff=cutoff, synthetic_only=True)


def scores(marks, names, weights, regime):
    """One cross-section at a time. Rankings have no probability meaning."""
    ids = sorted(marks)
    if not ids:
        return {}
    ranks = {}
    for i,name in enumerate(names):
        values = _ranks([marks[s][i] for s in ids])
        ranks[name] = dict(zip(ids, (2*v/max(1,len(ids)-1)-1 for v in values)))
    out = {"equal_weight":{s:0. for s in ids}}
    for name,(feature,direction) in RULES.items():
        if feature in ranks:
            out[name] = {s:direction*ranks[feature][s] for s in ids}
    if weights:
        out["multi_factor"] = {s:sum(w*ranks[f][s] for f,w in weights.items()) for s in ids}
    if "momentum" in out and "low_volatility" in out:
        out["regime_adaptive"] = (out["low_volatility"] if regime in ("downtrend","high_volatility")
                                  else out["momentum"])
    return out
