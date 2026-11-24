"""Adaptive synthetic ensemble with bounded daily working memory."""
from __future__ import annotations
from collections import deque
from itertools import zip_longest,groupby
from math import exp
from statistics import fmean
from atlaspm.engine.contracts import finite


def iter_ensemble(predictions, *, window_dates=20, minimum_dates=5, temperature=.01, regimes=None):
    """Input streams must be ordered by (date, security_id); yield daily rows."""
    if type(window_dates) is not int or type(minimum_dates) is not int or not 1<=minimum_dates<=window_dates:
        raise ValueError("INVALID_ENSEMBLE_WINDOW")
    temp=finite(temperature,"temperature",minimum=.000001)
    names=sorted(predictions)
    if len(names)<2:
        raise ValueError("ENSEMBLE_REQUIRES_TWO_MODELS")
    def aligned():
        previous=None
        for rows in zip_longest(*(iter(predictions[n]) for n in names)):
            if any(r is None for r in rows):
                raise ValueError("UNALIGNED_ENSEMBLE_FORECASTS")
            base=rows[0]
            key=(base["date"],base["security_id"])
            if previous is not None and key<=previous:
                raise ValueError("DUPLICATE_OR_UNSORTED_FORECAST")
            previous=key
            for row in rows:
                if (row["date"],row["security_id"])!=key:
                    raise ValueError("UNALIGNED_ENSEMBLE_FORECASTS")
                if (row["actual"],row["label_end_date"])!=(base["actual"],base["label_end_date"]):
                    raise ValueError("INCONSISTENT_ENSEMBLE_LABEL")
                if row["label_end_date"]<=row["date"]:
                    raise ValueError("INVALID_LABEL_MATURITY")
                finite(row["prediction"],"prediction")
                if row["actual"] is not None:
                    finite(row["actual"],"actual")
            yield rows
    pending=deque()
    matured=deque(maxlen=window_dates)
    conditioned={}
    seen=False
    for day,group in groupby(aligned(),key=lambda rows:rows[0]["date"]):
        seen=True
        while pending and pending[0][0]<day:
            _,state,errors=pending.popleft()
            if errors is not None:
                matured.append(errors)
                if regimes is not None and state!="warmup":
                    conditioned.setdefault(state,deque(maxlen=window_dates)).append(errors)
        state=regimes[day] if regimes is not None else None
        local=conditioned.get(state,())
        selected=local if len(local)>=minimum_dates else matured
        if len(selected)>=minimum_dates:
            loss={n:fmean(d[n] for d in selected) for n in names}
            best=min(loss.values())
            raw={n:exp(-(loss[n]-best)/temp) for n in names}
            weights={n:raw[n]/sum(raw.values()) for n in names}
        else:
            weights={n:1/len(names) for n in names}
        output=[]
        errors={n:[] for n in names}
        maturity=day
        for rows in group:
            base=rows[0]
            output.append({**base,"prediction":sum(weights[n]*row["prediction"] for n,row in zip(names,rows))})
            maturity=max(maturity,base["label_end_date"])
            if base["actual"] is not None:
                for n,row in zip(names,rows):
                    errors[n].append(abs(row["prediction"]-row["actual"]))
        if pending and maturity<pending[-1][0]:
            raise ValueError("NONMONOTONIC_LABEL_MATURITY")
        # Only scalar daily loss summaries are queued until label maturity.
        pending.append((maturity,state,{n:fmean(v) for n,v in errors.items()} if errors[names[0]] else None))
        audit=dict(date=day,matured_dates=len(matured),weights=weights)
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
