"""Chronological OOS uncertainty, performance drift and research-only model selection."""
from __future__ import annotations

from itertools import groupby
from math import ceil
from statistics import fmean
from atlaspm.research.training_registry import FLAGS
from atlaspm.engine.contracts import finite
from atlaspm.research.training_predictions import aligned_forecasts, MaturedDateWindow


def monitor_predictions(streams,*,window_dates=30,minimum_dates=10,coverage=.9,
                        drift_ratio=1.5,baseline="historical_mean",minimum_improvement=.05,
                        minimum_label_fraction=1.):
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
    window=MaturedDateWindow(window_dates,minimum_label_fraction=minimum_label_fraction)
    audit=[]
    total={m:0 for m in models}
    hits={m:0 for m in models}
    covered_dates={m:0 for m in models}
    assessed_dates={m:0 for m in models}

    aligned=aligned_forecasts(streams,models,
        mismatch="MONITOR_ALIGNMENT_MISMATCH",
        order="MONITOR_DATE_OR_SECURITY_ORDER",
        label_mismatch="MONITOR_ALIGNMENT_MISMATCH",
        maturity="MONITOR_LABEL_MATURITY")

    def admit(day):
        for item in window.advance(day):
            # Assess coverage on the admitted calibration population only;
            # a partial day cannot count as a fully covered cross-section.
            if item["eligible"]:
                for model in models:
                    if item["intervals"][model] is not None:
                        total[model]+=item["observed_count"]
                        hits[model]+=item["hits"][model]
                        if item["observed_count"]==item["forecast_count"]:
                            assessed_dates[model]+=1
                            covered_dates[model]+=int(item["hits"][model]==item["forecast_count"])

    for day,group in groupby(aligned,key=lambda rows:rows[0]["date"]):
        admit(day)
        past=list(window.history)
        recent_blocks=past[-window_dates:]
        older_blocks=past[:-window_dates]
        recent=[r for r in recent_blocks if r["eligible"]]
        older=[r for r in older_blocks if r["eligible"]]
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
                          matured_date_slots=len(recent_blocks),
                          incomplete_matured_dates=len(recent_blocks)-len(recent),
                          champion=champion,models=diagnostics))
        losses={m:0. for m in models}
        maxima={m:0. for m in models}
        matches={m:0 for m in models}
        maturity=None
        count=0
        forecast_count=0
        for bundle in group:
            row=bundle[0]
            forecast_count+=1
            end=row["label_end_date"]
            maturity=end if maturity is None else max(end,maturity)
            if row["actual"] is None:
                continue
            count+=1
            for model,forecast in zip(models,bundle):
                error=abs(forecast["prediction"]-row["actual"])
                losses[model]+=error
                maxima[model]=max(maxima[model],error)
                if intervals[model] is not None and error<=intervals[model]:
                    matches[model]+=1
        window.append(maturity=maturity,forecast_count=forecast_count,observed_count=count,
                      intervals=intervals,hits=matches,
                      mae={m:losses[m]/count for m in models} if count else {},
                      max_error=maxima if count else {})
    if not audit:
        raise ValueError("EMPTY_MONITOR_PREDICTIONS")
    latest=audit[-1]
    review={}
    for model in models:
        data=latest["models"][model]
        if model==baseline:
            status="REFERENCE_BASELINE"
        elif latest["matured_calibration_dates"]<minimum_dates:
            status="INSUFFICIENT_EVIDENCE"
        elif data["drift_alert"] is True:
            status="REVIEW_FOR_DEGRADATION"
        elif latest["champion"]==model:
            status="CANDIDATE_FOR_INDEPENDENT_REVIEW"
        else:
            status="RETAIN_AS_CHALLENGER"
        review[model]=dict(status=status,as_of=latest["date"],
                           independent_review_required=True,automatic_retirement=False)
    return dict(daily=audit,baseline=baseline,nominal_coverage=coverage,
                model_review=review,
                coverage_as_of_last_prediction={m:dict(
                    sample_count=total[m],empirical_row_coverage=hits[m]/total[m] if total[m] else None,
                    assessed_dates=assessed_dates[m],all_rows_covered_fraction=covered_dates[m]/assessed_dates[m] if assessed_dates[m] else None)
                    for m in models},
                interval_method="rolling_matured_daily_max_absolute_residual",
                interval_coverage_guaranteed=False,automatic_promotion_enabled=False,
                minimum_label_fraction=window.minimum_label_fraction,
                coverage_population="matured_eligible_dates; full cross-section coverage requires all labels",
                pending_unmatured_dates=len(window.pending),**FLAGS)
