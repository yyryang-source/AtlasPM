"""Adaptive synthetic ensemble: weights depend only on matured past OOS labels."""
from __future__ import annotations
from collections import defaultdict, deque
from math import exp
from statistics import fmean
from atlaspm.engine.contracts import finite


def adaptive_ensemble(predictions, *, window_dates=20, minimum_dates=5, temperature=.01):
    if type(window_dates) is not int or type(minimum_dates) is not int or not 1<=minimum_dates<=window_dates:
        raise ValueError("INVALID_ENSEMBLE_WINDOW")
    temp=finite(temperature,"temperature",minimum=.000001)
    names=sorted(predictions)
    if len(names)<2:
        raise ValueError("ENSEMBLE_REQUIRES_TWO_MODELS")
    tables={}
    for name in names:
        table={}
        for row in predictions[name]:
            key=(row["date"],row["security_id"])
            if key in table or row["label_end_date"]<=row["date"]:
                raise ValueError("DUPLICATE_OR_INVALID_FORECAST")
            finite(row["prediction"],"prediction")
            if row["actual"] is not None:
                finite(row["actual"],"actual")
            table[key]=row
        tables[name]=table
    keys=sorted(tables[names[0]])
    if not keys or any(set(tables[n])!=set(keys) for n in names):
        raise ValueError("UNALIGNED_ENSEMBLE_FORECASTS")
    grouped=defaultdict(list)
    for key in keys:
        rows=[tables[n][key] for n in names]
        if any((r["actual"],r["label_end_date"])!=(rows[0]["actual"],rows[0]["label_end_date"]) for r in rows):
            raise ValueError("INCONSISTENT_ENSEMBLE_LABEL")
        grouped[key[0]].append(key)
    # A whole cross-sectional date matures at its last label end. This avoids
    # partial cross-section weighting and gives every observed date equal weight.
    pending=deque()
    matured=deque(maxlen=window_dates)
    output, audit=[],[]
    for day,day_keys in sorted(grouped.items()):
        while pending and pending[0][0]<day:
            _,past=pending.popleft()
            errors={}
            for name in names:
                rows=[tables[name][k] for k in past if tables[name][k]["actual"] is not None]
                if rows:
                    errors[name]=fmean(abs(r["prediction"]-r["actual"]) for r in rows)
            if len(errors)==len(names):
                matured.append(errors)
        if len(matured)>=minimum_dates:
            loss={n:fmean(d[n] for d in matured) for n in names}
            best=min(loss.values())
            raw={n:exp(-(loss[n]-best)/temp) for n in names}
            weights={n:raw[n]/sum(raw.values()) for n in names}
        else:
            weights={n:1/len(names) for n in names}
        audit.append(dict(date=day,matured_dates=len(matured),weights=weights))
        for key in day_keys:
            base=tables[names[0]][key]
            output.append({**base,"prediction":sum(weights[n]*tables[n][key]["prediction"] for n in names)})
        maturity=max(tables[names[0]][k]["label_end_date"] for k in day_keys)
        if pending and maturity<pending[-1][0]:
            raise ValueError("NONMONOTONIC_LABEL_MATURITY")
        pending.append((maturity,day_keys))
    return dict(predictions=output,weight_history=audit,research_only=True,
                approved_for_model_training=False,live_execution_enabled=False,
                broker_order_submission_enabled=False,model_registry_admission=False)
