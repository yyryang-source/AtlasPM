"""Chronological OOS uncertainty, performance drift and research-only model selection."""
from __future__ import annotations

from collections import deque
from datetime import date
from itertools import groupby,zip_longest
from math import ceil
from statistics import fmean
from atlaspm.research.training_registry import FLAGS
from atlaspm.engine.contracts import finite


def monitor_predictions(streams,*,window_dates=30,minimum_dates=10,coverage=.9,
                        drift_ratio=1.5,baseline="historical_mean",minimum_improvement=.05):
    """Use past matured date blocks only. Daily max residual calibrates a
    cross-sectional interval; empirical coverage is not a guarantee under drift.
    Champion status means research candidate, never operational approval.
    """
    if not isinstance(streams,dict) or not streams or baseline not in streams:
        raise ValueError("MONITOR_BASELINE_REQUIRED")
    if type(window_dates) is not int or type(minimum_dates) is not int or not 2<=minimum_dates<=window_dates<=252:
        raise ValueError("INVALID_MONITOR_WINDOW")
    coverage=finite(coverage,"coverage")
    ratio=finite(drift_ratio,"drift_ratio")
    improvement=finite(minimum_improvement,"minimum_improvement")
    if not .5<coverage<1 or ratio<=1 or not 0<=improvement<1:
        raise ValueError("INVALID_MONITOR_THRESHOLDS")
    models=sorted(streams)
    history=deque(maxlen=2*window_dates)
    pending=deque()
    audit=[]
    previous=None
    total={m:0 for m in models}
    hits={m:0 for m in models}
    covered_dates={m:0 for m in models}
    assessed_dates={m:0 for m in models}

    def aligned():
        for bundle in zip_longest(*(iter(streams[m]) for m in models)):
            if any(r is None for r in bundle):
                raise ValueError("MONITOR_ALIGNMENT_MISMATCH")
            first=bundle[0]
            key=(first["date"],first["security_id"],first["label_end_date"],first["actual"])
            if any((r["date"],r["security_id"],r["label_end_date"],r["actual"])!=key for r in bundle):
                raise ValueError("MONITOR_ALIGNMENT_MISMATCH")
            if date.fromisoformat(key[2])<=date.fromisoformat(key[0]):
                raise ValueError("MONITOR_LABEL_MATURITY")
            for row in bundle:
                finite(row["prediction"],"prediction")
            if key[3] is not None:
                finite(key[3],"actual")
            yield first["date"],bundle

    def admit(day):
        while pending and pending[0]["maturity"]<day:
            item=pending.popleft()
            if item["count"]:
                history.append(item)
                for model in models:
                    if item["intervals"][model] is not None:
                        total[model]+=item["count"]
                        hits[model]+=item["hits"][model]
                        assessed_dates[model]+=1
                        covered_dates[model]+=int(item["hits"][model]==item["count"])

    for day,group in groupby(aligned(),key=lambda x:x[0]):
        if previous is not None and day<=previous:
            raise ValueError("MONITOR_DATE_ORDER")
        previous=day
        admit(day)
        recent=list(history)[-window_dates:]
        older=list(history)[:-window_dates]
        intervals={}
        diagnostics={}
        for model in models:
            mae=fmean(r["mae"][model] for r in recent) if recent else None
            ref=fmean(r["mae"][model] for r in older) if older else None
            scores=sorted(r["max_error"][model] for r in recent)
            # Finite-sample split conformal rank. Refuse an unbounded quantile.
            rank=ceil((len(scores)+1)*coverage)
            radius=scores[rank-1] if len(scores)>=minimum_dates and rank<=len(scores) else None
            intervals[model]=radius
            diagnostics[model]=dict(recent_mae=mae,reference_mae=ref,
                interval_half_width=radius,drift_alert=(mae>ratio*ref if
                mae is not None and ref is not None and len(older)>=minimum_dates else None))
        champion=baseline
        base=diagnostics[baseline]["recent_mae"]
        if len(recent)>=minimum_dates and base is not None:
            best=min(models,key=lambda m:(diagnostics[m]["recent_mae"],m))
            if diagnostics[best]["recent_mae"]<base*(1-improvement) and diagnostics[best]["drift_alert"] is not True:
                champion=best
        audit.append(dict(date=day,matured_calibration_dates=len(recent),
                          champion=champion,models=diagnostics))
        losses={m:[] for m in models}
        matches={m:0 for m in models}
        maturity=None
        previous_id=None
        count=0
        for _,bundle in group:
            row=bundle[0]
            sid=row["security_id"]
            if previous_id is not None and sid<=previous_id:
                raise ValueError("MONITOR_SECURITY_ORDER")
            previous_id=sid
            end=row["label_end_date"]
            maturity=end if maturity is None else max(end,maturity)
            if row["actual"] is None:
                continue
            count+=1
            for model,forecast in zip(models,bundle):
                error=abs(forecast["prediction"]-row["actual"])
                losses[model].append(error)
                if intervals[model] is not None and error<=intervals[model]:
                    matches[model]+=1
        if pending and maturity<pending[-1]["maturity"]:
            raise ValueError("MONITOR_MATURITY_ORDER")
        pending.append(dict(maturity=maturity,count=count,intervals=intervals,hits=matches,
            mae={m:fmean(losses[m]) for m in models} if count else {},
            max_error={m:max(losses[m]) for m in models} if count else {}))
    return dict(daily=audit,baseline=baseline,nominal_coverage=coverage,
                coverage_as_of_last_prediction={m:dict(
                    sample_count=total[m],empirical_row_coverage=hits[m]/total[m] if total[m] else None,
                    assessed_dates=assessed_dates[m],all_rows_covered_fraction=covered_dates[m]/assessed_dates[m] if assessed_dates[m] else None)
                    for m in models},
                interval_method="rolling_matured_daily_max_absolute_residual",
                interval_coverage_guaranteed=False,automatic_promotion_enabled=False,
                pending_unmatured_dates=len(pending),**FLAGS)
