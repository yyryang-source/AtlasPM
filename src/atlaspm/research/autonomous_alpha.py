"""Autonomous Alpha research orchestration; synthetic only, no live authority."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import json

from atlaspm.engine.contracts import finite, timestamp
from atlaspm.research.training_dataset import build_panel, MarketRegimes, validated_sessions, validate_panel_metadata
from atlaspm.research.training_engine import train, _fingerprint
from atlaspm.research.training_registry import FLAGS, digest, register, local_path
from atlaspm.research.alpha_factory import factor_selection, scores
from atlaspm.research.alpha_metrics import summary, eligible, selection_score


def research(payload, *, validation_start, holdout_start, models=None, cost_bps=10.,
             minimum_sessions=10, max_drawdown=.2, artifact_path=None, registry_path=None,
             sequence_length=1, seed=42):
    """One fixed research tournament, not production strategy approval.
    
    Validation chooses once; holdout can veto but never switch the winner.
    All candidate models share the existing purged training engine. The
    one-session horizon prevents overlapping portfolio return intervals.
    """
    names = validate_panel_metadata(payload,1,sequence_length)
    for key,value in FLAGS.items():
        if key in payload and payload[key] is not value:
            raise ValueError("ALPHA_PERMISSION_OVERRIDE")
    sessions = payload["sessions"]
    if (not isinstance(sessions,list) or len(sessions)>10000 or len(names)>64
            or sum(len(s["assets"]) for s in sessions)>200000):
        raise ValueError("ALPHA_SYNTHETIC_PANEL_LIMIT")
    if type(minimum_sessions) is not int or minimum_sessions < 10:
        raise ValueError("ALPHA_MINIMUM_EVIDENCE")
    cost_bps = finite(cost_bps,"cost_bps",minimum=0)
    max_drawdown = finite(max_drawdown,"max_drawdown",minimum=0)
    if max_drawdown > 1 or cost_bps >= 1000:
        raise ValueError("INVALID_ALPHA_RISK_CONFIG")
    days = [s["date"] for s in sessions]
    if days != sorted(set(days)):
        raise ValueError("SESSION_ORDER")
    if validation_start not in days or holdout_start not in days:
        raise ValueError("ALPHA_SPLIT_NOT_SESSION")
    vi,hi = days.index(validation_start),days.index(holdout_start)
    if vi < 20+sequence_length+2 or hi-vi < minimum_sessions+2 or len(days)-hi < minimum_sessions+2:
        raise ValueError("ALPHA_INSUFFICIENT_CHRONOLOGICAL_PERIODS")
    # Exclude the day whose label matures on the holdout boundary.
    validation_days = days[vi:hi-1]
    holdout_days = days[hi:-1]
    # Fit factor direction and redundancy on matured training prefix only.
    prefix = {**payload,"sessions":sessions[:vi]}
    panel = build_panel(prefix,1)
    factors = factor_selection(panel["samples"],names,validation_start)
    del panel
    tracker = MarketRegimes()
    latest = {}
    daily_rules = {}
    # Only prefix data is visible to candidate scoring and training.
    for day, marks in validated_sessions(sessions[:hi],names):
        tracker.observe(day,marks)
        if day >= validation_start:
            daily_rules[day] = scores({s:v[1] for s,v in marks.items()},names,
                                      factors["weights"],tracker.states[day])
    models = ["ridge","historical_mean"] if models is None else models
    options = dict(horizon=1,minimum_train_sessions=20,test_sessions=5,embargo_sessions=1,
                   models=models,sequence_length=sequence_length,seed=seed)
    if len(models)>1:
        options.update(ensemble={},regime_ensemble={})
    tuning = train({**payload,"sessions":sessions[:hi]},**options)
    predictions = tuning["predictions"]
    reference = predictions[models[0]]
    rule_names = sorted(daily_rules[validation_start])
    def rule_rows(reference, name):
        return ({**row,"prediction":daily_rules[row["date"]][name][row["security_id"]]}
                for row in reference if row["date"] in daily_rules)
    streams = {"model:"+k:v for k,v in predictions.items()}
    for name in rule_names:
        streams["rule:"+name] = lambda name=name: rule_rows(reference,name)
    validation = {}
    for name, rows in streams.items():
        validation[name] = summary(rows,validation_days,cost_bps=cost_bps,
                                   trials=len(streams),regimes=tracker.states)
    qualified = [n for n,m in validation.items() if eligible(m,minimum_sessions=minimum_sessions,
                                                            max_drawdown=max_drawdown)]
    winner = min(qualified,key=lambda n:(-selection_score(validation[n]),n)) if qualified else None
    validation_folds = tuning["folds"]
    missing_label_paths = tuning["missing_label_paths"]
    del streams, predictions, reference, tuning
    daily_rules.clear()
    # The candidate identity is locked before any holdout feature is read.
    # Validate even a NO_TRADE input; never ignore corrupted later data.
    for day, marks in validated_sessions(sessions[hi:],names):
        if day <= days[hi-1]:
            raise ValueError("SESSION_ORDER")
        tracker.observe(day,marks)
        if winner and winner.startswith("rule:"):
            latest = scores({s:v[1] for s,v in marks.items()},names,
                            factors["weights"],tracker.states[day])
            daily_rules[day] = latest
    trained = None
    holdout = None
    dataset = _fingerprint(payload)
    if winner:
        name = winner.split(":",1)[1]
        selected_options = {k:v for k,v in options.items() if k not in ("ensemble","regime_ensemble")}
        if winner.startswith("rule:"):
            selected_options["models"] = ["historical_mean"]
        elif name in ("adaptive_ensemble","regime_ensemble"):
            selected_options = dict(options)
        else:
            selected_options["models"] = [name]
        if artifact_path is not None and winner.startswith("model:") and name in models:
            selected_options["model_artifact_path"] = str(local_path(artifact_path))
        trained = train(payload,**selected_options)
        dataset = trained["dataset_sha256"]
        missing_label_paths = trained["missing_label_paths"]
        if winner.startswith("rule:"):
            rows = lambda: rule_rows(trained["predictions"]["historical_mean"],name)
        else:
            rows = trained["predictions"][name]
        holdout = summary(rows,holdout_days,cost_bps=cost_bps,trials=1,
                          regimes=tracker.states)
    accepted = bool(holdout and eligible(holdout,minimum_sessions=minimum_sessions,max_drawdown=max_drawdown))
    config = dict(validation_start=validation_start,holdout_start=holdout_start,models=models,
                  cost_bps=cost_bps,minimum_sessions=minimum_sessions,max_drawdown=max_drawdown,
                  sequence_length=sequence_length,seed=seed)
    strategy_id = digest(dict(candidate=winner,feature_names=names,
                              engine_version="autonomous_alpha_v1")) if winner else None
    version = digest(dict(strategy_id=strategy_id,dataset=dataset,config=config,
                          factor_weights=factors["weights"])) if winner else None
    final_scores = {}
    artifact_id = None
    if accepted and winner.startswith("rule:"):
        final_scores = latest[winner.removeprefix("rule:")]
    elif accepted and artifact_path is not None and winner.removeprefix("model:") in models:
        from atlaspm.research.training_artifacts import ModelArtifacts, predict_snapshot
        name = winner.removeprefix("model:")
        artifact_id = trained["folds"][-1].get("model_artifact_ids",{}).get(name)
        if artifact_id:
            record = ModelArtifacts(artifact_path).load(artifact_id)
            if record["dataset_sha256"] != dataset or record["model"] != name:
                raise ValueError("ALPHA_ARTIFACT_IDENTITY_MISMATCH")
            result = predict_snapshot(record,{**payload,"sessions":sessions[-sequence_length:]})
            final_scores = {r["security_id"]:r["prediction"] for r in result["predictions"]}
    # Do not turn an uncalibrated score into an expected return or probability.
    signals = []
    if final_scores:
        close = timestamp(sessions[-1]["close_at"],"close_at")
        assets = {a["security_id"]:a for a in sessions[-1]["assets"]}
        from atlaspm.research.training_engine import _ranks
        ids = sorted(final_scores)
        ranks = _ranks([final_scores[s] for s in ids])
        strength = dict(zip(ids, (v/max(1,len(ids)-1) for v in ranks)))
        for rank,sid in enumerate(sorted(ids,key=lambda s:(-final_scores[s],s)),1):
            signals.append(dict(security_id=sid,symbol=sid.removeprefix("SYN:"),
                asset_type="equity",sector=assets[sid].get("sector","Unclassified"),
                strategy_id=strategy_id,model_version=artifact_id or version,
                factor_score=2*strength[sid]-1,model_rank=rank,
                signal_direction="LONG_CANDIDATE" if strength[sid]>=.75 else "NEUTRAL",signal_strength=strength[sid],
                expected_holding_sessions=1,risk_classification="UNASSESSED",
                generated_at=close.isoformat(),available_at=close.isoformat(),
                data_available_at=assets[sid]["available_at"],
                expires_at=(close+timedelta(hours=24)).isoformat(),
                identity_source="synthetic",data_source="synthetic",data_quality="synthetic_test_only",
                validation_status="SYNTHETIC_HOLDOUT_PASSED",research_eligible=True,
                model_approved_for_training=False))
    report = dict(version="autonomous_alpha_v1",dataset_sha256=dataset,
                  config=config, factor_selection=factors, validation=validation,
                  selected_strategy=winner, strategy_id=strategy_id,strategy_version=version,
                  holdout=holdout,holdout_mode="locked_candidate_walk_forward_refit",
                  strategy_status="VALIDATED" if accepted else "SUSPENDED" if winner else "RESEARCH",
                  lifecycle_history=["RESEARCH","VALIDATING"]+(["VALIDATED"] if accepted else ["SUSPENDED"]),
                  decision="SYNTHETIC_RESEARCH_SIGNAL" if signals else "NO_TRADE",
                  market_state=tracker.states[days[-1]],market_state_history=tracker.states,
                  market_state_probability=None,model_artifact_id=artifact_id,
                  signals=signals, validation_folds=validation_folds,
                  training_folds=trained["folds"] if trained else [],
                  missing_label_paths=missing_label_paths,
                  missing_label_scope="full_panel" if trained else "validation_prefix",
                  holdout_candidates_evaluated=[winner] if winner else [],
                  **FLAGS)
    if registry_path is not None:
        report["registry_run_id"] = register(local_path(registry_path),config,report,kind="experiment")
        if version:
            from atlaspm.research.alpha_lifecycle import track_run, history
            track_run(registry_path,report["registry_run_id"])
            lifecycle = history(registry_path,version)
            report["persistent_lifecycle_state"] = lifecycle["state"]
            if lifecycle["state"] != "VALIDATED":
                report["signals"] = []
                report["decision"] = "NO_TRADE"
    return report


def simulate(payload, portfolio, **kwargs):
    """In-process synthetic bridge; no externally supplied approval report.
    
    The current portfolio engine requires every existing holding to appear
    in the candidate universe and does not support opening new positions.
    Never reinterpret rankings as expected returns.
    """
    from atlaspm.engine.unified import run_unified_portfolio
    if portfolio.get("mode")!="synthetic" or portfolio.get("synthetic_fixture") is not True:
        raise ValueError("ALPHA_REAL_PORTFOLIO_BLOCKED")
    for key,value in FLAGS.items():
        if key in portfolio and portfolio[key] is not value:
            raise ValueError("ALPHA_PORTFOLIO_PERMISSION_OVERRIDE")
    report = research(payload,**kwargs)
    if not report["signals"]:
        return dict(research=report,portfolio=None,decision="NO_TRADE",**FLAGS)
    if timestamp(portfolio["as_of"],"as_of") != timestamp(payload["sessions"][-1]["close_at"],"close_at"):
        raise ValueError("ALPHA_PORTFOLIO_TIME_MISMATCH")
    signals = {s["symbol"]:s for s in report["signals"]}
    symbols = portfolio["market"]["symbols"]
    if not 1<=len(symbols)<=20 or set(symbols)!=set(signals):
        raise ValueError("ALPHA_PORTFOLIO_UNIVERSE_MISMATCH")
    if portfolio.get("constraints",{}).get("method","equal_weight") != "equal_weight":
        raise ValueError("ALPHA_UNCALIBRATED_RETURN_OPTIMIZER_BLOCKED")
    latest_assets = {a["security_id"]:a for a in payload["sessions"][-1]["assets"]}
    for holding in portfolio["account"]["positions"]:
        sid = "SYN:"+holding["symbol"]
        if sid not in latest_assets or abs(finite(holding["mark_price"],"mark_price")-latest_assets[sid]["close"])>1e-6:
            raise ValueError("ALPHA_PORTFOLIO_MARK_MISMATCH")
        if holding["sector"] != signals[holding["symbol"]]["sector"]:
            raise ValueError("ALPHA_PORTFOLIO_SECTOR_MISMATCH")
    # This bridge validates the contracts and OMS, not alpha-driven sizing.
    result = run_unified_portfolio({**portfolio,"signals":[signals[s] for s in symbols]})
    return dict(research=report,portfolio=result,decision=result["decision"],**FLAGS)


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",required=True,type=Path)
    args = parser.parse_args(argv)
    request = json.loads(args.file.read_text(encoding="utf-8"))
    result = simulate(**request) if "portfolio" in request else research(**request)
    json.dump(result,sys.stdout,indent=2,allow_nan=False)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
