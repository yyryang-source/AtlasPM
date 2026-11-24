"""Bounded synthetic chronological research-to-paper replay.

No external forecast inputs, broker connection, or real market admission.
Generate on close t, execute using observed marks at close t+1, evaluate
the reconciled account at close t+2. Only the synthetic calendar is known ahead.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from math import isfinite
from atlaspm.engine.contracts import Account, finite, timestamp
from atlaspm.research.training_autonomous import _code_version, _project
from atlaspm.research.training_dataset import validated_sessions, validate_panel_metadata
from atlaspm.research.training_engine import train, _fingerprint
from atlaspm.research.training_registry import FLAGS, load, local_path, register
from atlaspm.research.training_decision_policy import paper_decision
from atlaspm.research.training_paper_feedback import record_paper_feedback
from atlaspm.research.training_sequence import SEQUENCE_MODELS


def _qualified(observed,registry_path,run_id):
    if observed.get("mode")!="synthetic" or observed.get("synthetic_fixture") is not True:
        raise ValueError("REAL_TRAINING_DATA_NOT_APPROVED")
    if any(k in observed and observed[k] is not v for k,v in FLAGS.items()):
        raise ValueError("RESEARCH_PERMISSION_OVERRIDE_DENIED")
    record=load(local_path(registry_path),run_id)
    report,config=record["report"],record["config"]
    selected=report.get("selected_research_strategy")
    if (record["kind"]!="experiment" or report.get("status")!="CHALLENGER_HOLDOUT_PASS_RESEARCH_ONLY"
        or report.get("holdout_consumed") is not True or not isinstance(selected,dict)
        or config.get("code_sha256")!=_code_version()):
        raise ValueError("NO_CURRENT_QUALIFIED_RESEARCH_CHALLENGER")
    sessions=observed["sessions"]
    frozen=[s for s in sessions if s["date"]<=config["holdout_end"]]
    if not frozen or frozen[-1]["date"]!=config["holdout_end"] or _fingerprint({**observed,"sessions":frozen})!=report["dataset_sha256"]:
        raise ValueError("FROZEN_RESEARCH_DATASET_MISMATCH")
    if sessions[-1]["date"]<=config["holdout_end"]:
        raise ValueError("FORECAST_MUST_FOLLOW_HOLDOUT")
    return record


def current_forecasts(observed, *, registry_path, run_id, next_session):
    """Reuse the purged trainer; no future label, price, or fake bar is supplied."""
    record=_qualified(observed,registry_path,run_id)
    selected=record["report"]["selected_research_strategy"]
    settings=dict(record["config"]["settings"])
    model=selected["model"]
    if settings["horizon"]!=1:
        raise ValueError("CONTINUOUS_REQUIRES_ONE_SESSION_HORIZON")
    # Validate the entire observed schema before feature projection.
    names=validate_panel_metadata(observed,1,settings.get("sequence_length",1))
    for _ in validated_sessions(observed["sessions"],names):
        pass
    projected=_project(observed,selected["features"])
    result=train(projected,**settings,models=[model,"historical_mean"],
                 ridge_alpha=selected["ridge_alpha"],next_session=next_session,
                 incremental_ridge=model=="ridge")
    day=observed["sessions"][-1]["date"]
    for rows in result["predictions"].values():
        latest=[r for r in rows if r["date"]==day]
        if not latest or any(r["actual"] is not None or r["label_end_date"]!=next_session for r in latest):
            raise ValueError("CURRENT_FORECAST_CONTRACT_FAILURE")
    return dict(date=day,next_session=next_session,model=model,
                streams=result["predictions"],observed_sha256=_fingerprint(observed),
                forecast_sha256=result["dataset_sha256"],dependency_versions=result["dependency_versions"],
                training_folds=result["folds"],**FLAGS)


def _marks(session):
    rows=session["assets"]
    result={r["security_id"]:finite(r["close"],"mark",minimum=.000001) for r in rows}
    if len(result)!=len(rows):
        raise ValueError("DUPLICATE_MARK")
    return result


def _market(sessions,symbols,lookback,shrinkage):
    import numpy as np
    marks=[_marks(s) for s in sessions[-lookback-1:]]
    ids=["SYN:"+s for s in symbols]
    if len(marks)<lookback+1 or any(set(ids)-m.keys() for m in marks):
        raise ValueError("INCOMPLETE_RISK_WINDOW")
    matrix=np.asarray([[m[s] for s in ids] for m in marks],dtype=float)
    returns=matrix[1:]/matrix[:-1]-1
    if not np.isfinite(returns).all():
        raise ValueError("NONFINITE_RISK_RETURNS")
    cov=np.atleast_2d(np.cov(returns,rowvar=False,ddof=1))*252
    # Shrink towards a diagonal target and add a small variance floor.
    cov=(1-shrinkage)*cov+shrinkage*np.diag(np.diag(cov))+np.eye(len(ids))*.0001
    return dict(symbols=symbols,covariance=cov.tolist(),
                expected_returns={s:0. for s in symbols},scenario_returns=returns.tolist())


def _equity(account,receivable):
    return Account.from_payload(account).net_liquidation+receivable


def run_continuous(payload, *, registry_path,run_id,account,constraints,paper=None,
                   max_steps=20,risk_lookback=30,covariance_shrinkage=.3,max_drawdown=.2):
    """Replay a bounded post-holdout interval; settle synthetic sales next session.

    Initial account must be synthetic and already hold all eligible names.
    Existing Unified Portfolio Engine still disallows opening new tickers.
    """
    if type(max_steps) is not int or not 1<=max_steps<=60 or type(risk_lookback) is not int or not 20<=risk_lookback<=252:
        raise ValueError("INVALID_CONTINUOUS_BUDGET")
    shrink=finite(covariance_shrinkage,"covariance_shrinkage",minimum=0)
    dd_limit=finite(max_drawdown,"max_drawdown",minimum=0)
    if shrink>1 or not 0<dd_limit<=1:
        raise ValueError("INVALID_CONTINUOUS_RISK_LIMIT")
    if not isinstance(constraints,dict) or not isinstance(paper or {},dict):
        raise ValueError("INVALID_CONTINUOUS_SETTINGS")
    record=load(local_path(registry_path),run_id)
    sessions=payload["sessions"]
    calendar=[s["date"] for s in sessions]
    if calendar!=sorted(set(calendar)) or any(date.fromisoformat(d).isoformat()!=d for d in calendar):
        raise ValueError("INVALID_CONTINUOUS_CALENDAR")
    end=record["config"]["holdout_end"]
    first=next((i for i,d in enumerate(calendar) if d>end),len(calendar))
    if first+2>=len(calendar) or first<risk_lookback:
        raise ValueError("INSUFFICIENT_POST_HOLDOUT_SESSIONS")
    _qualified({**payload,"sessions":sessions[:first+1]},registry_path,run_id)
    state=deepcopy(account)
    initial=Account.from_payload(state)
    if not initial.account_id.startswith("SYN-") or not initial.holdings:
        raise ValueError("SYNTHETIC_HELD_ACCOUNT_REQUIRED")
    sectors={h.symbol:h.sector for h in initial.holdings}
    rate=finite(constraints.get("annual_margin_rate",.065),"annual_margin_rate",minimum=0)
    if rate>1:
        raise ValueError("INVALID_CONTINUOUS_FINANCING_RATE")
    # Never trust an externally supplied net liquidation for subsequent marks.
    state.pop("broker_reported_net_liquidation",None)
    pending=None
    receivable=0.
    daily=[]
    # Start accounting at the first signal close, using observed prices only.
    previous_time=timestamp(sessions[first]["close_at"],"initial_close")
    for p in state["positions"]:
        p.update(mark_price=_marks(sessions[first])["SYN:"+p["symbol"]],
                 mark_age_seconds=0,mark_source="synthetic")
    initial_equity=_equity(state,0.)
    peak=initial_equity
    stop=min(first+max_steps+1,len(sessions)-1)
    for i in range(first+1,stop+1):
        # Last iteration marks the previous decision but creates no unobservable trade.
        execution=sessions[i]
        now=timestamp(execution["close_at"],"execution_close")
        if now.date().isoformat()!=execution["date"] or now<=previous_time:
            raise ValueError("INVALID_EXECUTION_CLOSE")
        # Validate all observations at the current close before valuation or settlement.
        list(validated_sessions([execution],payload["feature_names"]))
        marks=_marks(execution)
        state["accrued_financing_interest"]=float(state.get("accrued_financing_interest",0))+state.get("margin_debt",0)*rate*(now-previous_time).total_seconds()/86400/365
        previous_time=now
        for pos in state["positions"]:
            pos.update(mark_price=marks["SYN:"+pos["symbol"]],mark_age_seconds=0,mark_source="synthetic")
        before_settlement=_equity(state,receivable)
        state["settled_cash"]+=receivable
        settled=receivable
        receivable=0.
        equity=_equity(state,0.)
        if abs(equity-before_settlement)>.01:
            raise ValueError("SYNTHETIC_SETTLEMENT_RECONCILIATION_FAILURE")
        matured=None
        if pending is not None:
            matured=record_paper_feedback(registry_path=registry_path,decision_id=pending,
                observation=dict(mode="synthetic",synthetic_fixture=True,
                    observed_at=execution["close_at"],available_at=execution["close_at"],
                    marks={p["symbol"]:p["mark_price"] for p in state["positions"]}),
                as_of=execution["close_at"])
            if abs(matured["ending_equity"]-equity)>.01:
                raise ValueError("MATURED_ACCOUNT_EQUITY_MISMATCH")
            pending=None
        peak=max(peak,equity)
        row=dict(date=execution["date"],opening_equity=equity,settled_sales=settled,
                 matured_feedback=matured,drawdown=equity/peak-1)
        if i==stop:
            row.update(decision="MARK_ONLY_FINAL_SESSION",ending_equity=equity)
            daily.append(row)
            break
        if row["drawdown"]<=-dd_limit or not state["positions"]:
            row.update(decision="NO_TRADE",reason="DRAWDOWN_LIMIT_OR_NO_HELD_UNIVERSE",ending_equity=equity)
            daily.append(row)
            continue
        observed={**payload,"sessions":sessions[:i]}
        forecast=current_forecasts(observed,registry_path=registry_path,run_id=run_id,
                                   next_session=execution["date"])
        symbols=[p["symbol"] for p in state["positions"]]
        current_ids={r["security_id"] for r in forecast["streams"][forecast["model"]] if r["date"]==forecast["date"]}
        if current_ids!={"SYN:"+s for s in symbols}:
            row.update(decision="NO_TRADE",reason="CURRENT_HELD_UNIVERSE_MISMATCH",ending_equity=equity)
            daily.append(row)
            continue
        signal_time=sessions[i-1]["close_at"]
        signals=[dict(symbol=s,security_id="SYN:"+s,identity_source="synthetic",sector=sectors[s],
            model_rank=j+1,factor_score=0.,model_version=run_id,generated_at=signal_time,
            available_at=signal_time,data_available_at=signal_time,expires_at=execution["close_at"],
            data_source="synthetic",data_quality="synthetic_test_only",research_eligible=True,
            model_approved_for_training=False) for j,s in enumerate(symbols)]
        market=_market(sessions[:i+1],symbols,risk_lookback,shrink)
        # Margin requirements remain unverified, and beta/real named scenarios are not invented.
        inputs=dict(mode="synthetic",synthetic_fixture=True,as_of=execution["close_at"],
                    account=state,signals=signals,market=market,constraints=constraints,
                    paper=paper or {},market_data_available_at=execution["close_at"],
                    history_available_at=execution["close_at"])
        out=paper_decision(inputs,forecast["streams"],registry_path=registry_path,run_id=run_id,
                          forecast_day=forecast["date"],execution_maturity_date=calendar[i+1])
        row.update(decision=out["decision"],reason=out.get("reason"),
                   signal_date=forecast["date"],forecast_sha256=forecast["forecast_sha256"],
                   risk_scale=out.get("risk_scale"),feedback_scale=out.get("feedback_scale"),
                   paper_decision_id=out.get("paper_decision_id"))
        if out["decision"]=="SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE":
            if not all(out["portfolio"]["reconciliation_checks"].values()):
                raise ValueError("PAPER_RECONCILIATION_REQUIRED")
            actual=out["portfolio"]["paper_oms"]["account_replay"]
            if actual["unsettled_payable"]!=0:
                raise ValueError("CONTINUOUS_PAYABLE_UNSUPPORTED")
            state.update(settled_cash=actual["settled_cash"],margin_debt=actual["margin_debt"],
                         accrued_financing_interest=actual["accrued_financing_interest"],
                         positions=[dict(symbol=p["identifier"],sector=sectors[p["identifier"]],
                            shares=int(p["shares"]),mark_price=p["mark_price"],mark_source="synthetic",
                            mark_age_seconds=0) for p in actual["positions"]])
            receivable=actual["unsettled_receivable"]
            pending=out["paper_decision_id"]
            if abs(_equity(state,receivable)-actual["marked_net_liquidation"])>.01:
                raise ValueError("CONTINUOUS_ACCOUNT_RECONCILIATION_FAILURE")
        row["ending_equity"]=_equity(state,receivable)
        daily.append(row)
    report=dict(status="SYNTHETIC_CONTINUOUS_REPLAY_COMPLETE",dataset_sha256=_fingerprint(
                    {**payload,"sessions":sessions[:stop+1]}),research_run_id=run_id,
                initial_equity=initial_equity,ending_equity=daily[-1]["ending_equity"],
                net_return=daily[-1]["ending_equity"]/initial_equity-1,
                steps=daily,account=state,unsettled_receivable=receivable,
                execution_model="close_t_plus_1_mark_t_plus_2",settlement_model="next_synthetic_session",
                capacity_verified=False,**FLAGS)
    key=register(local_path(registry_path),dict(research_run_id=run_id,account_id=initial.account_id,
        max_steps=max_steps,risk_lookback=risk_lookback,covariance_shrinkage=shrink,
        max_drawdown=dd_limit,constraints=constraints,paper=paper or {}),report,kind="experiment")
    return {**report,"replay_id":key}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file",type=Path,required=True)
    p.add_argument("--forecast-only",action="store_true")
    a=p.parse_args()
    inputs=json.loads(a.file.read_text(encoding="utf-8"))
    print(json.dumps((current_forecasts if a.forecast_only else run_continuous)(**inputs),
                     indent=2,allow_nan=False))


if __name__=="__main__":
    main()
