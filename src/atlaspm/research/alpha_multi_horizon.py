"""Synthetic multi-horizon Alpha research with a common locked holdout.

Reuse the existing bounded tournament and purged trainer. All horizon
winners are selected on a shared chronological prefix before any winner
sees the holdout. Metrics are predictive diagnostics, not trade returns.
"""
from __future__ import annotations

import argparse
from itertools import groupby
import json
from pathlib import Path

from atlaspm.research import training_tournament as tournament
from atlaspm.research.training_dataset import HORIZONS, validate_panel_metadata, validated_sessions
from atlaspm.research.training_engine import _fingerprint, _metrics
from atlaspm.research.training_registry import FLAGS, digest

SCHEMA = "atlaspm_multi_horizon_alpha_research_v1"
MAX_ROWS = 200000
MAX_CANDIDATES = 8
MAX_FOLD_FITS = 2000


def _plan(payload, horizons, config, candidates, holdout_start, metric):
    if (not isinstance(horizons, (tuple, list)) or not 2 <= len(horizons) <= 5
            or any(type(h) is not int or h not in HORIZONS for h in horizons)
            or len(set(horizons)) != len(horizons)):
        raise ValueError("MULTI_HORIZON_REQUIRES_DISTINCT_SUPPORTED_HORIZONS")
    if not isinstance(config, dict) or set(config) - (tournament.COMMON - {"horizon"}):
        raise ValueError("INVALID_MULTI_HORIZON_CONFIG")
    if metric not in tournament.METRICS:
        raise ValueError("INVALID_TOURNAMENT_METRIC")
    names = validate_panel_metadata(payload, horizons[0], config.get("sequence_length", 1))
    for key, value in FLAGS.items():
        if key in payload and payload[key] is not value:
            raise ValueError("MULTI_HORIZON_PERMISSION_OVERRIDE")
    sessions = payload["sessions"]
    if (not isinstance(sessions, list) or len(sessions) > 2000 or len(names) > 64
            or not 0 < len(sessions) or
            sum(len(s["assets"]) for s in sessions) > MAX_ROWS):
        raise ValueError("MULTI_HORIZON_PANEL_BUDGET")
    if not isinstance(candidates, list) or not 2 <= len(candidates) <= MAX_CANDIDATES:
        raise ValueError("MULTI_HORIZON_CANDIDATE_BUDGET")
    minimum = config.get("minimum_train_sessions", 20)
    step = config.get("test_sessions", 4)
    embargo = config.get("embargo_sessions", 1)
    length = config.get("sequence_length", 1)
    if any(type(x) is not int or x < lo for x, lo in ((minimum,1),(step,1),(embargo,0))):
        raise ValueError("INVALID_FOLD_CONFIG")
    if type(holdout_start) is not int:
        raise ValueError("INVALID_HOLDOUT_INDEX")
    configs = {}
    planned_fits = 0
    for h in sorted(horizons):
        first = minimum + h + embargo + length - 1
        if (not first + h < holdout_start < len(sessions)-h
                or (holdout_start-first) % step):
            raise ValueError("MULTI_HORIZON_COMMON_HOLDOUT_MUST_ALIGN_ALL_REFIT_BOUNDARIES")
        cfg = {**config, "test_sessions": step, "horizon": h}
        options = [tournament._trial_config(c, cfg) for c in candidates]
        subsets = [c.get("features", names) for c in candidates]
        for subset in subsets:
            if (not isinstance(subset, list) or not subset
                    or any(not isinstance(n, str) or n not in names for n in subset)
                    or len(set(subset)) != len(subset)):
                raise ValueError("INVALID_FEATURE_SUBSET")
        if len({digest([opt, subset]) for opt, subset in zip(options, subsets)}) != len(options):
            raise ValueError("DUPLICATE_TOURNAMENT_CANDIDATE")
        planned_fits += len(candidates)*len(range(first,holdout_start-h,step))
        planned_fits += len(range(first,len(sessions)-h,step))
        configs[h] = cfg
    if planned_fits > MAX_FOLD_FITS:
        raise ValueError("MULTI_HORIZON_FOLD_FIT_BUDGET")
    return names, configs, planned_fits


def _holdout_diagnostics(rows, dates, expected, horizon, start):
    # Fixed spacing, decided solely by the session calendar, not by which
    # predictions performed well. Missing cohorts are not silently dropped.
    index = {day:i for i,day in enumerate(dates)}
    planned = dates[start:len(dates)-horizon:horizon]
    groups = {}
    last = None
    for day, group in groupby(rows, key=lambda r:r["date"]):
        if last is not None and day <= last:
            raise ValueError("MULTI_HORIZON_PREDICTIONS_NOT_CHRONOLOGICAL")
        last = day
        i = index.get(day)
        if i is None or not start <= i < len(dates)-horizon:
            raise ValueError("MULTI_HORIZON_PREDICTION_OUTSIDE_HOLDOUT")
        seen = set()
        items = list(group)
        for row in items:
            sid = row["security_id"]
            if sid in seen or sid not in expected[day]:
                raise ValueError("MULTI_HORIZON_DUPLICATE_OR_UNKNOWN_SECURITY")
            seen.add(sid)
            if row["label_end_date"] != dates[i+horizon]:
                raise ValueError("MULTI_HORIZON_LABEL_MATURITY_MISMATCH")
        if day in planned:
            groups[day] = items
    complete = []
    missing = []
    for day in planned:
        items = groups.get(day, [])
        if ({r["security_id"] for r in items} != expected[day]
                or any(r["actual"] is None for r in items)):
            missing.append(day)
        else:
            complete.extend(items)
    return {
        "planned_nonoverlapping_dates": len(planned),
        "complete_nonoverlapping_dates": len(planned)-len(missing),
        "missing_or_incomplete_dates": missing,
        "complete_cohort_metrics": _metrics(complete),
        "coverage_complete": bool(planned) and not missing,
        "sampling": "fixed_holdout_start_then_every_horizon_sessions",
        "economic_return_or_execution_simulated": False,
    }


def research(payload, *, holdout_start, horizons=(1,5,21), config=None,
             candidates=None, selection_metric="mae"):
    config = {} if config is None else config
    candidates = [{"model":"historical_mean"},{"model":"ridge"}] if candidates is None else candidates
    names, configs, planned_fits = _plan(payload,horizons,config,candidates,
                                         holdout_start,selection_metric)
    sessions = payload["sessions"]
    prefix = {**payload,"sessions":sessions[:holdout_start]}
    locks = {}
    # Selection-only uses the prefix, but the tournament's boundary checks
    # need the full length. Pass only the length/date metadata for later
    # sessions, never holdout asset values into a selection call.
    planning_payload = {**prefix, "sessions":prefix["sessions"]+
                        [{"date":s["date"]} for s in sessions[holdout_start:]]}
    for h,cfg in configs.items():
        selected = tournament.tournament(planning_payload,config=cfg,
                    candidates=candidates,holdout_start=holdout_start,
                    selection_metric=selection_metric,selection_only=True)
        if selected["holdout_evaluated"] is not False:
            raise ValueError("MULTI_HORIZON_SELECTION_CONTRACT")
        locks[h] = selected
    lock_sha = digest({"prefix_sha256":_fingerprint(prefix),"locks":locks})
    # Only now validate/consume holdout features and outcomes. No candidate
    # replacement or re-selection is allowed after this point.
    dates = []
    expected = {}
    for day,marks in validated_sessions(sessions,names):
        dates.append(day)
        if len(dates)-1 >= holdout_start:
            expected[day] = set(marks)
    reports = {}
    for h,locked in locks.items():
        winner = locked["selected_model"]
        final = tournament.train(tournament.project_features(payload,locked["selected_features"]),
                                 **locked["selected_config"])
        day = dates[holdout_start]
        if day not in {f["test_start"] for f in final["folds"]}:
            raise ValueError("MULTI_HORIZON_REFIT_BOUNDARY_MISMATCH")
        rows = [r for r in final["predictions"][winner] if r["date"] >= day]
        if not rows:
            raise ValueError("NO_HOLDOUT_PREDICTIONS")
        diagnostics = _holdout_diagnostics(rows,dates,expected,h,holdout_start)
        reports[str(h)] = {
            "selection":locked,
            "holdout":_metrics(rows),
            "nonoverlapping_diagnostic":diagnostics,
            "training_dataset_sha256":final["dataset_sha256"],
            "holdout_candidates_evaluated":[winner],
            "status":"DIAGNOSTIC_COMPLETE" if diagnostics["coverage_complete"]
                     else "INCOMPLETE_COHORT_REVIEW_REQUIRED",
        }
        del rows,final
    return {
        "schema":SCHEMA, **FLAGS,
        "dataset_sha256":_fingerprint(payload),
        "selection_lock_sha256":lock_sha,
        "holdout_start":dates[holdout_start],
        "holdout_mode":"all_horizons_locked_before_any_holdout_training",
        "selection_metric":selection_metric,
        "horizons":reports, "planned_fold_fits_upper_bound":planned_fits,
        "all_nonoverlapping_cohorts_complete":all(
            r["nonoverlapping_diagnostic"]["coverage_complete"] for r in reports.values()),
        "decision":"NO_TRADE", "signals":[], "approved_for_backtest":False,
        "status":"SYNTHETIC_MULTI_HORIZON_RESEARCH_COMPLETE",
        "warning":("Selection periods differ by horizon; model errors are not "
                   "comparable across horizons. Nonoverlapping diagnostics "
                   "are predictive only and do not establish independent "
                   "samples, economic alpha or production eligibility."),
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(research(**json.loads(args.file.read_text(encoding="utf-8"))),
                     indent=2,allow_nan=False))


if __name__=="__main__":
    main()
