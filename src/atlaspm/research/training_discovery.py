"""Bounded, execution-aligned synthetic Alpha discovery.

Search only a fixed expression grammar on a chronological validation prefix.
The chosen rule alone enters a fresh reserved holdout. No real market training,
model admission, paper promotion or broker permissions are granted.
"""
from __future__ import annotations

import argparse
import json
from itertools import combinations
from statistics import fmean
from pathlib import Path
from atlaspm.engine.contracts import finite
from atlaspm.research.training_autonomous import _code_version, _reserve_holdout
from atlaspm.research.training_dataset import validate_panel_metadata, validated_sessions
from atlaspm.research.training_engine import _fingerprint, _ranks, _correlation
from atlaspm.research.training_registry import FLAGS, digest, local_path, register
from atlaspm.research.training_strategy_metrics import evaluate, paired_lower


def _grammar(features):
    """Deterministic finite grammar; no eval, external code or free-form AST."""
    # Enumerate both orientations for each primitive before pair expressions.
    for name in sorted(features):
        for sign in (1,-1):
            yield dict(op="rank",features=[name],sign=sign)
    for a,b in combinations(sorted(features),2):
        for op in ("sum","difference","interaction"):
            for sign in (1,-1):
                yield dict(op=op,features=[a,b],sign=sign)


def _check_rule(rule,features):
    if not isinstance(rule,dict) or set(rule)!={"op","features","sign"}:
        raise ValueError("INVALID_FACTOR_RULE")
    op=rule["op"]
    names=rule["features"]
    if op not in ("rank","sum","difference","interaction") or type(rule["sign"]) is not int or rule["sign"] not in (-1,1):
        raise ValueError("INVALID_FACTOR_RULE")
    if not isinstance(names,list) or len(names)!=(1 if op=="rank" else 2) or any(n not in features for n in names) or len(set(names))!=len(names):
        raise ValueError("INVALID_FACTOR_RULE_FEATURES")


def _score_day(marks,features,rule):
    """Use only the cross section available on this session; tie-aware ranks."""
    ids=sorted(marks)
    if len(ids)<2:
        raise ValueError("FACTOR_REQUIRES_TWO_ASSETS")
    vectors=[]
    for name in rule["features"]:
        j=features.index(name)
        ranks=_ranks([marks[s][1][j] for s in ids])
        vectors.append([2*r/(len(ids)-1)-1 for r in ranks])
    a=vectors[0]
    op=rule["op"]
    if op=="rank":
        out=a
    else:
        b=vectors[1]
        if op=="sum":
            out=[(x+y)/2 for x,y in zip(a,b)]
        elif op=="difference":
            out=[(x-y)/2 for x,y in zip(a,b)]
        else:
            out=[x*y for x,y in zip(a,b)]
    return dict(zip(ids,[rule["sign"]*v for v in out]))


def _observations(payload, *, max_rows):
    if any(k in payload and payload[k] is not v for k,v in FLAGS.items()):
        raise ValueError("RESEARCH_PERMISSION_OVERRIDE_DENIED")
    features=validate_panel_metadata(payload,1,1)
    if len(features)>128:
        raise ValueError("DISCOVERY_FEATURE_LIMIT")
    count=0
    result=[]
    for day,marks in validated_sessions(payload["sessions"],features):
        count+=len(marks)
        if count>max_rows:
            raise ValueError("DISCOVERY_ROW_BUDGET_EXCEEDED")
        result.append((day,marks))
    return features,result


def _rows(observed,features,rule,start,stop,*,minimum_symbols):
    """Signal at t, close t+1 entry, close t+2 exit/label maturity."""
    if rule is not None:
        _check_rule(rule,features)
    rows=[]
    for i in range(start,stop-2):
        day,marks=observed[i]
        _,entry=observed[i+1]
        maturity,exit_=observed[i+2]
        if len(marks)<minimum_symbols:
            raise ValueError("DISCOVERY_MINIMUM_UNIVERSE_NOT_MET")
        if marks.keys()-entry.keys() or marks.keys()-exit_.keys():
            raise ValueError("MISSING_EXECUTABLE_PATH")
        scores=_score_day(marks,features,rule) if rule is not None else dict.fromkeys(marks,0.)
        for sid in sorted(marks):
            rows.append(dict(date=day,security_id=sid,label_end_date=maturity,
                prediction=scores[sid],actual=exit_[sid][0]/entry[sid][0]-1))
    return rows


def _redundancy(a,b):
    """Mean absolute daily rank correlation, counting only nonconstant days."""
    from itertools import groupby
    values=[]
    for (_,aa),(_,bb) in zip(groupby(a,key=lambda r:r["date"]),groupby(b,key=lambda r:r["date"]),strict=True):
        aa,bb=list(aa),list(bb)
        if [(r["date"],r["security_id"]) for r in aa]!=[(r["date"],r["security_id"]) for r in bb]:
            raise ValueError("DISCOVERY_ALIGNMENT_MISMATCH")
        ric=_correlation(_ranks([r["prediction"] for r in aa]),_ranks([r["prediction"] for r in bb]))
        if ric is not None:
            values.append(abs(ric))
    return fmean(values) if values else None


def _stability(rows):
    """Three chronological blocks, not an iid row split."""
    from itertools import groupby
    daily=[]
    for _,group in groupby(rows,key=lambda r:r["date"]):
        group=list(group)
        value=_correlation(_ranks([r["prediction"] for r in group]),_ranks([r["actual"] for r in group]))
        daily.append(value)
    n=len(daily)
    blocks=[]
    for i in range(3):
        block=[v for v in daily[i*n//3:(i+1)*n//3] if v is not None]
        blocks.append(fmean(block) if block else None)
    return dict(block_rank_ic=blocks,valid_dates=sum(v is not None for v in daily),
                positive_blocks=sum(v is not None and v>0 for v in blocks))


def discover(payload,*,registry_path,holdout_start,momentum_feature,budget=24,
             max_rows=100000,minimum_symbols=5,minimum_periods=30,
             redundancy_threshold=.95,cost_bps=10.,top_fraction=.25,
             bootstrap_draws=5000,bootstrap_block=5,seed=42):
    """One-use validation and holdout; select at most one executable rule."""
    for v,lo,hi in ((budget,2,48),(max_rows,100,1000000),(minimum_symbols,2,10000),
                    (minimum_periods,20,1000),(bootstrap_draws,1000,10000),
                    (bootstrap_block,2,10),(seed,0,2**31-1)):
        if type(v) is not int or not lo<=v<=hi:
            raise ValueError("INVALID_DISCOVERY_BUDGET")
    threshold=finite(redundancy_threshold,"redundancy_threshold")
    cost=finite(cost_bps,"cost_bps",minimum=0)
    fraction=finite(top_fraction,"top_fraction",minimum=.000001)
    if not .5<=threshold<=1 or cost>=10000 or fraction>1 or minimum_periods<2*bootstrap_block:
        raise ValueError("INVALID_DISCOVERY_THRESHOLDS")
    features,observed=_observations(payload,max_rows=max_rows)
    if momentum_feature not in features:
        raise ValueError("MOMENTUM_BASELINE_FEATURE_REQUIRED")
    if type(holdout_start) is not int or holdout_start<minimum_periods+2 or len(observed)-holdout_start<minimum_periods+2:
        raise ValueError("INSUFFICIENT_DISCOVERY_HOLDOUT")
    from itertools import islice
    rules=list(islice(_grammar(features),budget))
    # Never infer statistical significance below the empirical bootstrap resolution.
    tail=.05/(2*len(rules))
    if bootstrap_draws*tail<2:
        raise ValueError("INSUFFICIENT_BOOTSTRAP_TAIL_RESOLUTION")
    prefix={**payload,"sessions":payload["sessions"][:holdout_start]}
    momentum=dict(op="rank",features=[momentum_feature],sign=1)
    config=dict(grammar="rank_pair_v1",rules=rules,momentum_feature=momentum_feature,
        minimum_symbols=minimum_symbols,minimum_periods=minimum_periods,max_rows=max_rows,
        redundancy_threshold=threshold,cost_bps=cost,top_fraction=fraction,
        bootstrap_draws=bootstrap_draws,bootstrap_block=bootstrap_block,seed=seed,
        holdout_start=observed[holdout_start][0],holdout_end=observed[-1][0],
        code_sha256=_code_version())
    path=local_path(registry_path)
    _reserve_holdout(path,observed[0][0],config["holdout_start"],config["holdout_end"],
                     digest({"config":config,"validation_dataset":_fingerprint(prefix)}))
    options=dict(top_fraction=fraction,cost_bps=cost,execution_aligned_labels=True)

    def compare(rows,begin,end):
        return evaluate(rows,payload["sessions"][begin:end],**options)

    def reference(begin,end):
        return {n:compare(_rows(observed,features,r,begin,end,minimum_symbols=minimum_symbols),begin,end)
                for n,r in (("equal_weight",None),("momentum",momentum))}

    references=reference(0,holdout_start)
    trials=[]
    # Store only the current best candidate scores for redundancy checks.
    best=None
    best_rows=None
    for rule in rules:
        rows=_rows(observed,features,rule,0,holdout_start,minimum_symbols=minimum_symbols)
        outcome=compare(rows,0,holdout_start)
        stability=_stability(rows)
        evidence={n:paired_lower(outcome,b,seed=seed,draws=bootstrap_draws,
                    block=bootstrap_block,tail=tail) for n,b in references.items()}
        reasons=[]
        if stability["positive_blocks"]!=3:
            reasons.append("UNSTABLE_RANK_IC")
        if any(v["lower_bound"]<=0 for v in evidence.values()):
            reasons.append("NO_MULTIPLICITY_ADJUSTED_NET_ADVANTAGE")
        corr=_redundancy(rows,best_rows) if best_rows is not None else None
        score=min(v["lower_bound"] for v in evidence.values())
        trial=dict(rule=rule,metrics=outcome["metrics"],stability=stability,
                   comparisons=evidence,score=score,redundancy_to_current_best=corr,
                   reasons=reasons)
        if not reasons:
            if best is None or score>best["score"]:
                if best is not None and corr is not None and corr>=threshold:
                    best["reasons"].append("REDUNDANT_TO_BETTER_RULE")
                best=trial
                best_rows=rows
            elif corr is not None and corr>=threshold:
                reasons.append("REDUNDANT_TO_CURRENT_BEST")
        trials.append(trial)
    selected=best["rule"] if best is not None else None
    validation=dict(dataset_sha256=_fingerprint(prefix),trials=trials,
        baselines={n:b["metrics"] for n,b in references.items()},selected_rule=selected,**FLAGS)
    validation_id=register(path,config,validation,kind="experiment")
    # The validation decision is complete before evaluating the holdout.
    final=reference(holdout_start,len(observed))
    comparison={}
    passed=False
    if selected is not None:
        rows=_rows(observed,features,selected,holdout_start,len(observed),minimum_symbols=minimum_symbols)
        final["selected"]=compare(rows,holdout_start,len(observed))
        comparison={n:paired_lower(final["selected"],final[n],seed=seed,
                    draws=bootstrap_draws,block=bootstrap_block,tail=.025)
                    for n in ("equal_weight","momentum")}
        passed=all(v["lower_bound"]>0 for v in comparison.values()) and _stability(rows)["positive_blocks"]==3
    report=dict(status="DISCOVERY_HOLDOUT_PASS_REVIEW_REQUIRED" if passed else "DISCOVERY_ADVANTAGE_NOT_ESTABLISHED",
        dataset_sha256=_fingerprint(payload),validation_run_id=validation_id,validation=validation,
        holdout={n:v["metrics"] for n,v in final.items()},holdout_comparison=comparison,
        selected_rule=selected if passed else None,holdout_consumed=True,
        label_definition="close_t_plus_2_over_close_t_plus_1_minus_one",
        automatic_promotion_enabled=False,capacity_verified=False,**FLAGS)
    key=register(path,config,report,kind="experiment")
    return {**report,"run_id":key}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(discover(**json.loads(args.file.read_text(encoding="utf-8"))),
                     indent=2,allow_nan=False))


if __name__=="__main__":
    main()
