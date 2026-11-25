"""Adaptive synthetic ensemble with bounded daily working memory."""
from __future__ import annotations
from itertools import groupby
from math import exp
from statistics import fmean
from atlaspm.engine.contracts import finite
from atlaspm.research.training_predictions import aligned_forecasts, MaturedDateWindow


def iter_ensemble(predictions, *, window_dates=20, minimum_dates=5, temperature=.01, regimes=None, reference_model=None, drift_ratio=None, minimum_improvement=0., minimum_label_fraction=1.):
    """Input streams must be ordered by (date, security_id); yield daily rows."""
    if type(window_dates) is not int or type(minimum_dates) is not int or not 1<=minimum_dates<=window_dates:
        raise ValueError("INVALID_ENSEMBLE_WINDOW")
    temp=finite(temperature,"temperature",minimum=.000001)
    names=sorted(predictions)
    if len(names)<2:
        raise ValueError("ENSEMBLE_REQUIRES_TWO_MODELS")
    if reference_model is not None and reference_model not in names:
        raise ValueError("ENSEMBLE_REFERENCE_MODEL_REQUIRED")
    if drift_ratio is not None:
        drift_ratio=finite(drift_ratio,"drift_ratio")
        if drift_ratio<=1 or reference_model is None:
            raise ValueError("INVALID_ENSEMBLE_DRIFT_RATIO")
    improvement=finite(minimum_improvement,"minimum_improvement")
    if not 0<=improvement<1 or (improvement and reference_model is None):
        raise ValueError("INVALID_ENSEMBLE_IMPROVEMENT")
    aligned=aligned_forecasts(predictions,names,
        mismatch="UNALIGNED_ENSEMBLE_FORECASTS",
        order="DUPLICATE_OR_UNSORTED_FORECAST",
        label_mismatch="INCONSISTENT_ENSEMBLE_LABEL",
        maturity="INVALID_LABEL_MATURITY")
    window=MaturedDateWindow(window_dates,minimum_label_fraction=minimum_label_fraction)
    seen=False
    for day,group in groupby(aligned,key=lambda rows:rows[0]["date"]):
        seen=True
        for _ in window.advance(day):
            pass
        state=regimes[day] if regimes is not None else None
        past=list(window.history)
        recent_blocks=past[-window_dates:]
        older_blocks=past[:-window_dates]
        recent=[b["errors"] for b in recent_blocks if b["eligible"]]
        older=[b["errors"] for b in older_blocks if b["eligible"]]
        # Do not resurrect old regime-specific evidence from outside the
        # current rolling date window merely because the regime recurs.
        local=[b["errors"] for b in recent_blocks if b["eligible"]
               and regimes is not None and state!="warmup" and b["regime"]==state]
        selected=local if len(local)>=minimum_dates else recent
        quarantined=[]
        if len(selected)>=minimum_dates:
            loss={n:fmean(d[n] for d in selected) for n in names}
            active=list(names)
            if reference_model is not None:
                for n in names:
                    if n==reference_model:
                        continue
                    degraded=drift_ratio is not None and len(older)>=minimum_dates and fmean(d[n] for d in recent)>drift_ratio*fmean(d[n] for d in older)
                    if degraded or loss[n]>=loss[reference_model]*(1-improvement):
                        quarantined.append(n)
                active=[n for n in names if n not in quarantined]
            best=min(loss[n] for n in active)
            raw={n:exp(-(loss[n]-best)/temp) if n in active else 0. for n in names}
            normalizer=sum(raw.values())
            weights={n:raw[n]/normalizer for n in names}
        elif reference_model is not None:
            weights={n:float(n==reference_model) for n in names}
        else:
            weights={n:1/len(names) for n in names}
        output=[]
        errors={n:0. for n in names}
        observed=0
        maturity=day
        for rows in group:
            base=rows[0]
            output.append({**base,"prediction":sum(weights[n]*row["prediction"] for n,row in zip(names,rows))})
            maturity=max(maturity,base["label_end_date"])
            if base["actual"] is not None:
                observed+=1
                for n,row in zip(names,rows):
                    errors[n]+=abs(row["prediction"]-row["actual"])
        # Only scalar daily loss summaries are queued until label maturity.
        window.append(maturity=maturity,forecast_count=len(output),observed_count=observed,
                      regime=state,errors={n:v/observed for n,v in errors.items()} if observed else {})
        audit=dict(date=day,matured_dates=len(recent),weights=weights,
                   matured_date_slots=len(recent_blocks),
                   incomplete_matured_dates=len(recent_blocks)-len(recent),
                   minimum_label_fraction=window.minimum_label_fraction)
        if reference_model is not None:
            audit.update(reference_model=reference_model,quarantined=quarantined,
                         fallback_to_reference=weights[reference_model]==1.)
        if regimes is not None:
            audit.update(regime=state,regime_matured_dates=len(local),
                         weighting_scope='regime' if len(local)>=minimum_dates else 'global_fallback')
        yield output,audit
    if not seen:
        raise ValueError("EMPTY_ENSEMBLE_FORECASTS")


def adaptive_ensemble(predictions, **kwargs):
    # Compatibility entry point accepts unsorted in-memory lists.
    ordered={n:sorted(rows,key=lambda r:(r["date"],r["security_id"])) for n,rows in predictions.items()}
    output,audit=[],[]
    for rows,weights in iter_ensemble(ordered,**kwargs):
        output.extend(rows)
        audit.append(weights)
    return dict(predictions=output,weight_history=audit,research_only=True,
                approved_for_model_training=False,live_execution_enabled=False,
                broker_order_submission_enabled=False,model_registry_admission=False)
