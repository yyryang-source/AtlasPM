"""Conservative same-horizon numeric fusion. Fixed experimental policy only.

The existing chronological training_ensemble and training_monitor own learned
weights and calibration; this boundary does not grant an artifact admission.
"""
from __future__ import annotations
from atlaspm.engine.contracts import finite
from atlaspm.research.financial_events import FLAGS
from atlaspm.research.research_contract import contract
from atlaspm.research.training_registry import digest


def fuse(rows, *, as_of, weights, max_disagreement=.05, max_age_seconds=86400):
    if not isinstance(rows,list) or not 1<=len(rows)<=128 or not isinstance(weights,dict) or len(weights)>32:
        raise ValueError("INVALID_FUSION_BATCH")
    cap=finite(max_disagreement,"max_disagreement",minimum=0)
    policy={k:finite(v,"weight",minimum=0) for k,v in weights.items()}
    if not policy or any(not isinstance(k,str) or not k for k in policy) or sum(policy.values())<=0:
        raise ValueError("INVALID_EXPERIMENTAL_WEIGHTS")
    validated=[contract(r,as_of=as_of,max_age_seconds=max_age_seconds) for r in rows]
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
        chosen=[max(group,key=lambda r:(policy[r["contract"]["model_version"]],r["research_id"]))
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
            total=sum(policy[r["contract"]["model_version"]] for r in chosen)
            effective={r["contract"]["model_version"]:policy[r["contract"]["model_version"]]/total for r in chosen}
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
            "weight_policy":"caller_fixed_experimental","policy_sha256":digest(policy),
            "historical_calibration_verified":False}
    return report


def historical_diagnostics(streams, *, window_dates=30, minimum_dates=10):
    """Reuse canonical matured-label monitor and adaptive ensemble, not another fitter."""
    from atlaspm.research.training_monitor import monitor_predictions
    from atlaspm.research.training_ensemble import adaptive_ensemble
    monitor=monitor_predictions(streams,window_dates=window_dates,minimum_dates=minimum_dates)
    ensemble=adaptive_ensemble(streams,window_dates=window_dates,minimum_dates=minimum_dates,
                              reference_model="historical_mean")
    return {**FLAGS,"monitor":monitor,"ensemble":ensemble,"weights_promoted":False}
