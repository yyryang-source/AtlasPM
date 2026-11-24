"""Conservative same-horizon numeric fusion. Fixed experimental policy only.

The existing chronological training_ensemble and training_monitor own learned
weights and calibration; this boundary does not grant an artifact admission.
"""
from __future__ import annotations
from atlaspm.engine.contracts import finite,timestamp
from math import exp,log
from atlaspm.research.financial_events import FLAGS
from atlaspm.research.research_contract import contract
from atlaspm.research.training_registry import digest


def fuse(rows, *, as_of, weights, max_disagreement=.05, max_age_seconds=86400, half_life_seconds=None):
    if not isinstance(rows,list) or not 1<=len(rows)<=128 or not isinstance(weights,dict) or len(weights)>32:
        raise ValueError("INVALID_FUSION_BATCH")
    cap=finite(max_disagreement,"max_disagreement",minimum=0)
    policy={k:finite(v,"weight",minimum=0) for k,v in weights.items()}
    if not policy or any(not isinstance(k,str) or not k for k in policy) or any(v>1e6 for v in policy.values()) or sum(policy.values())<=0:
        raise ValueError("INVALID_EXPERIMENTAL_WEIGHTS")
    if half_life_seconds is not None:
        half_life_seconds=finite(half_life_seconds,"half_life_seconds",minimum=1)
    cutoff=timestamp(as_of,"as_of")
    validated=[contract(r,as_of=as_of,max_age_seconds=max_age_seconds) for r in rows]
    def weight(r):
        base=policy[r["contract"]["model_version"]]
        if half_life_seconds is None:
            return base
        oldest=min(timestamp(e["received_at"],"received_at") for e in r["contract"]["evidence"])
        return base*exp(-log(2)*min(60,(cutoff-oldest).total_seconds()/half_life_seconds))
    groups={}
    for r in validated:
        c=r["contract"]
        key=(c["security_id"],c["asset_type"],c["horizon_days"])
        groups.setdefault(key,[]).append(r)
    results=[]
    for (sid,asset,horizon),items in sorted(groups.items()):
        numeric=[r for r in items if r["numeric_eligible"] and policy.get(r["contract"]["model_version"],0)>0]
        # Reject duplicate model observations, and do not count shared evidence
        # as independent corroboration. Select one highest-weight candidate per
        # overlapping evidence component (transitive closure).
        names=[r["contract"]["model_version"] for r in numeric]
        if len(set(names))!=len(names):
            raise ValueError("DUPLICATE_MODEL_FORECAST")
        components=[]
        for r in numeric:
            refs={e["sha256"] for e in r["contract"]["evidence"]}
            members=[r]
            for i in range(len(components)-1,-1,-1):
                if refs.intersection(components[i][0]):
                    old,group=components.pop(i)
                    refs.update(old)
                    members.extend(group)
            components.append((refs,members))
        chosen=[max(group,key=lambda r:(weight(r),r["research_id"]))
                for _,group in components]
        vals=[r["contract"]["expected_return"] for r in numeric]
        spread=max(vals)-min(vals) if vals else None
        blocked=[]
        if len(chosen)<2:
            blocked.append("INSUFFICIENT_DISTINCT_EVIDENCE_GROUPS")
        if spread is not None and spread>cap:
            blocked.append("MODEL_DISAGREEMENT_LIMIT")
        if vals and min(vals)<0<max(vals):
            blocked.append("DIRECTION_CONFLICT")
        estimate=radius=lower=upper=None
        effective={}
        if chosen:
            total=sum(weight(r) for r in chosen)
            effective={r["contract"]["model_version"]:weight(r)/total for r in chosen}
            estimate=sum(effective[r["contract"]["model_version"]]*r["contract"]["expected_return"] for r in chosen)
            # Envelope, not a statistically calibrated interval; no reduction
            # of uncertainty simply because there are more models.
            lower=min(r["contract"]["expected_return"]-r["contract"]["uncertainty_radius"] for r in numeric)
            upper=max(r["contract"]["expected_return"]+r["contract"]["uncertainty_radius"] for r in numeric)
            radius=max(estimate-lower,upper-estimate)
        results.append({"security_id":sid,"asset_type":asset,"horizon_days":horizon,
            "research_ids":sorted(r["research_id"] for r in items),
            "numeric_count":len(numeric),"distinct_evidence_groups":len(chosen),
            "disagreement":spread,"expected_return":estimate,"uncertainty_radius":radius,
            "lower_bound":lower,"upper_bound":upper,"experimental_weights":effective,
            "blocked_reasons":blocked,"synthetic_decision_eligible":not blocked,
            "statistically_calibrated":False,"correlation_estimated":False})
    report={**FLAGS,"model":"conservative_fusion_v1","as_of":as_of,"groups":results,
            "weight_policy":"caller_fixed_experimental","policy_sha256":digest([policy,half_life_seconds]),
            "half_life_seconds":half_life_seconds,
            "historical_calibration_verified":False}
    return report


def historical_diagnostics(streams, *, window_dates=30, minimum_dates=10):
    """Reuse canonical matured-label monitor and adaptive ensemble, not another fitter."""
    if (not isinstance(streams,dict) or not 2<=len(streams)<=8 or
            any(not isinstance(rows,list) or not 1<=len(rows)<=10000 or
                any(not isinstance(r,dict) or not str(r.get("security_id","")).startswith("SYN:") for r in rows)
                for rows in streams.values())):
        raise ValueError("BOUNDED_SYNTHETIC_DIAGNOSTICS_ONLY")
    from atlaspm.research.training_monitor import monitor_predictions
    from atlaspm.research.training_ensemble import adaptive_ensemble
    monitor=monitor_predictions(streams,window_dates=window_dates,minimum_dates=minimum_dates)
    ensemble=adaptive_ensemble(streams,window_dates=window_dates,minimum_dates=minimum_dates,
                              reference_model="historical_mean")
    return {**FLAGS,"monitor":monitor,"ensemble":ensemble,"weights_promoted":False}


def matured_correlations(streams, *, as_of_date, minimum_dates=5):
    """Pairwise residual correlations on matured synthetic forecast dates.

    Daily cross-sectional mean error avoids counting every security as an
    independent date. Never automatically suppress or promote a model.
    """
    from datetime import date
    from itertools import groupby,combinations
    from statistics import mean,correlation,StatisticsError
    from atlaspm.research.training_predictions import aligned_forecasts
    cutoff=date.fromisoformat(as_of_date)
    if (type(minimum_dates) is not int or not 2<=minimum_dates<=252 or
        not isinstance(streams,dict) or not 2<=len(streams)<=8 or
        any(not isinstance(rows,list) or not 1<=len(rows)<=10000 for rows in streams.values())):
        raise ValueError("INVALID_CORRELATION_SAMPLE")
    names=sorted(streams)
    aligned=aligned_forecasts(streams,names,mismatch="ALIGNMENT",order="ORDER",
                              label_mismatch="LABEL_MISMATCH",maturity="MATURITY")
    values={name:[] for name in names}
    for _,group in groupby(aligned,key=lambda rows:rows[0]["date"]):
        errors={name:[] for name in names}
        for rows in group:
            if not rows[0]["security_id"].startswith("SYN:"):
                raise ValueError("SYNTHETIC_CORRELATION_ONLY")
            if rows[0]["actual"] is None or date.fromisoformat(rows[0]["label_end_date"])>=cutoff:
                continue
            for name,row in zip(names,rows):
                errors[name].append(row["prediction"]-row["actual"])
        if errors[names[0]]:
            for name in names:
                values[name].append(mean(errors[name]))
    pairs=[]
    n=len(values[names[0]])
    for left,right in combinations(names,2):
        value=None
        if n>=minimum_dates:
            try:
                value=correlation(values[left],values[right])
            except StatisticsError:
                pass
        pairs.append({"models":[left,right],"correlation":value,"matured_dates":n,
                      "redundancy_review":value is not None and abs(value)>=.9})
    return {**FLAGS,"pairs":pairs,"as_of_date":as_of_date,"weights_promoted":False}
