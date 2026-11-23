"""Offline prequential model tournament for dynamic cash-only rebalancing.

Every model is trained on expanding, fully observed past labels. At each
decision, the champion (or ensemble weights) is computed exclusively from
predictions made on EARLIER rebalance dates whose forward risk labels have
ALREADY matured. Neither future performance nor the final backtest winner
participates in selection. This is research, not a live trading service.
"""

from __future__ import annotations

from collections import Counter
from math import isfinite
from pathlib import Path
from statistics import fmean
from typing import Any

from atlaspm.backtest import _metrics
from atlaspm.historical import load_adjusted_prices
from atlaspm.ml_dynamic_rebalance import (
    _features, _forecast, _integer, _real, _rms, _select_allocation,
    _train_asset_model,
)
from atlaspm.ml_lightgbm import (
    _dependency, predict_lightgbm_risk, train_lightgbm_risk,
)
from atlaspm.risk_backtest import _trade_to_risk_budget

CANDIDATES = ("trailing_vol", "ridge", "lightgbm")
POLICY_NAME = "rolling_matured_prequential_risk_mae_v1"


def score_matured_forecasts(
    history: list[dict[str, Any]],
    by_asset: list[list[float]],
    *,
    current_signal_index: int,
    forecast_days: int,
    window_rebalances: int,
    minimum_rebalances: int,
) -> dict[str, Any]:
    """Score ONLY previously emitted forecasts with labels fully known by now.

    A forecast made using price index s is labeled with daily returns in
    [s,s+forecast_days), whose LAST close is at price index s+forecast_days.
    The label is usable iff s+forecast_days <= current_signal_index.
    Last-N means last N *completed forecast dates*, not last N assets.
    """
    if not by_asset:
        raise ValueError("At least one historical asset return series required")
    if minimum_rebalances < 1 or window_rebalances < minimum_rebalances:
        raise ValueError("Validation minimum must fit in its rolling window")
    matured = [
        h for h in history
        if h["signal_index"] + forecast_days <= current_signal_index
    ]
    selected = matured[-window_rebalances:]
    if len(selected) < minimum_rebalances:
        return {
            "is_validated": False,
            "completed_forecast_dates": len(selected),
            "completed_asset_predictions": len(selected) * len(by_asset),
            "mae_by_model": None,
            "last_completed_signal_index": (
                selected[-1]["signal_index"] if selected else None
            ),
            "latest_label_end_price_index": (
                selected[-1]["signal_index"] + forecast_days if selected else None
            ),
        }
    errors: dict[str, list[float]] = {name: [] for name in CANDIDATES}
    for event in selected:
        s = event["signal_index"]
        if s + forecast_days > current_signal_index:
            raise ValueError("Future forecast label would leak into champion scoring")
        if set(event["forecasts"]) != set(CANDIDATES):
            raise ValueError("Missing candidate forecasts in evaluation history")
        for j, returns in enumerate(by_asset):
            if s + forecast_days > len(returns):
                raise ValueError("Forecast label outside price returns")
            observed = _rms(returns[s:s+forecast_days])
            for name in CANDIDATES:
                candidate = event["forecasts"][name][j]
                if not isfinite(candidate) or candidate <= 0:
                    raise ValueError("Invalid historical candidate risk forecast")
                errors[name].append(abs(candidate - observed))
    scores = {key: fmean(value) for key, value in errors.items()}
    return {
        "is_validated": True,
        "completed_forecast_dates": len(selected),
        "completed_asset_predictions": len(selected) * len(by_asset),
        "mae_by_model": scores,
        "last_completed_signal_index": selected[-1]["signal_index"],
        "latest_label_end_price_index": selected[-1]["signal_index"]+forecast_days,
    }


def choose_tournament_weights(
    evaluation: dict[str, Any], *,
    mode: str,
    previous_champion: str = "trailing_vol",
    switch_margin: float = 0.02,
) -> dict[str, Any]:
    """Time-local best-model or inverse-MAE combination, baseline by default.

    Only a statistically limited rolling MAE comparison, not a formal test
    of economic utility. The untrained naive model is allowed to win.
    """
    if mode not in {"champion", "ensemble"}:
        raise ValueError("mode must be champion or ensemble")
    if previous_champion not in CANDIDATES:
        raise ValueError("Unknown preceding champion")
    margin = _real(switch_margin, "switch_margin", 0, .5)
    no_training = {
        "trailing_vol": 1.0, "ridge": 0.0, "lightgbm": 0.0
    }
    if not evaluation.get("is_validated"):
        return {
            "reason": "INSUFFICIENT_COMPLETED_VALIDATION_FALLBACK",
            "champion": "trailing_vol",
            "weights": no_training,
            "validation_is_ready": False,
        }
    scores = evaluation["mae_by_model"]
    if not isinstance(scores, dict) or set(scores) != set(CANDIDATES):
        raise ValueError("Invalid completed forecast scores")
    if any(not isfinite(scores[k]) or scores[k] < 0 for k in CANDIDATES):
        raise ValueError("Invalid nonnegative forecast MAEs")
    naive = scores["trailing_vol"]
    best_ml = min(("ridge", "lightgbm"), key=lambda k: (scores[k], k))
    if scores[best_ml] >= naive * (1 - margin):
        return {
            "reason": "NO_CLEAR_ML_ADVANTAGE_FALLBACK",
            "champion": "trailing_vol",
            "weights": no_training,
            "validation_is_ready": True,
        }
    if mode == "champion":
        winner = best_ml
        if (
            previous_champion != "trailing_vol"
            and scores[previous_champion] < naive * (1 - margin)
            and scores[previous_champion] <= scores[best_ml] * (1 + margin)
        ):
            winner = previous_champion
        return {
            "reason": "MATURED_ROLLING_OOS_MAE_CHAMPION",
            "champion": winner,
            "weights": {k: 1.0 if k == winner else 0.0 for k in CANDIDATES},
            "validation_is_ready": True,
        }
    # Positive, bounded inverse-error weights; zero-error fits cannot become
    # unbounded. Baseline also participates to damp fragile model gains.
    inverse = {key: 1.0 / max(.002, scores[key]) ** 2 for key in CANDIDATES}
    denom = sum(inverse.values())
    weights = {key: inverse[key] / denom for key in CANDIDATES}
    return {
        "reason": "MATURED_ROLLING_OOS_INVERSE_MAE_ENSEMBLE",
        "champion": max(CANDIDATES, key=lambda k: (weights[k], -CANDIDATES.index(k))),
        "weights": weights,
        "validation_is_ready": True,
    }


def backtest_model_tournament(
    path: Path,
    *,
    mode: str = "champion",
    feature_days: int = 20,
    forecast_days: int = 5,
    minimum_training_samples: int = 60,
    rebalance_days: int = 10,
    validation_window: int = 36,
    minimum_validated_rebalances: int = 6,
    switch_margin: float = .02,
    target_annual_volatility: float = .18,
    maximum_name_weight: float = .70,
    maximum_gross_exposure: float = 1.0,
    uniform_stress_decline: float = .30,
    maximum_uniform_stress_loss: float = .25,
    maximum_daily_tail_loss: float = .04,
    account_drawdown_trigger: float = .10,
    drawdown_exposure_cap: float = .25,
    fee_bps: float = 5.0,
    slippage_bps: float = 5.0,
) -> dict[str, Any]:
    """Run genuinely time-local risk-model selection and historical rebalances."""
    if mode not in {"champion", "ensemble"}:
        raise ValueError("Tournament mode must be champion or ensemble")
    feature_days = _integer(feature_days, "feature_days", 5, 252)
    forecast_days = _integer(forecast_days, "forecast_days", 2, 63)
    minimum_training_samples = _integer(
        minimum_training_samples, "minimum_training_samples", 20, 5000
    )
    rebalance_days = _integer(rebalance_days, "rebalance_days", 1, 252)
    validation_window = _integer(validation_window, "validation_window", 2, 252)
    minimum_validated_rebalances = _integer(
        minimum_validated_rebalances, "minimum_validated_rebalances", 2, 252
    )
    if minimum_validated_rebalances > validation_window:
        raise ValueError("Validation window must include minimum mature dates")
    switch_margin = _real(switch_margin, "switch_margin", 0, .5)
    target_annual_volatility = _real(
        target_annual_volatility, "target_annual_volatility", .001, 2
    )
    maximum_name_weight = _real(maximum_name_weight, "maximum_name_weight", .05, 1)
    maximum_gross_exposure = _real(
        maximum_gross_exposure, "maximum_gross_exposure", 0, 1
    )
    uniform_stress_decline = _real(
        uniform_stress_decline, "uniform_stress_decline", 0, 1
    )
    maximum_uniform_stress_loss = _real(
        maximum_uniform_stress_loss, "maximum_uniform_stress_loss", 0, 1
    )
    maximum_daily_tail_loss = _real(
        maximum_daily_tail_loss, "maximum_daily_tail_loss", 0, 1
    )
    account_drawdown_trigger = _real(
        account_drawdown_trigger, "account_drawdown_trigger", .001, 1
    )
    drawdown_exposure_cap = _real(
        drawdown_exposure_cap, "drawdown_exposure_cap", 0, 1
    )
    fee_bps = _real(fee_bps, "fee_bps", 0, 1000)
    slippage_bps = _real(slippage_bps, "slippage_bps", 0, 1000)
    _dependency()  # Explicit opt-in, error on missing optional native package

    prices = load_adjusted_prices(path)
    symbols = sorted(prices)
    if not 2 <= len(symbols) <= 12:
        raise ValueError("Model tournament requires 2 to 12 risky symbols")
    if len(symbols) * maximum_name_weight < 1-1e-12:
        raise ValueError("Maximum name weight infeasible for this asset count")
    dates = sorted(set.intersection(*(set(prices[s]) for s in symbols)))
    first_exec = feature_days + forecast_days + minimum_training_samples
    if len(dates) <= first_exec + 2:
        raise ValueError("Not enough aligned historical dates for training/testing")
    rows = [[prices[s][d] for s in symbols] for d in dates]
    daily = [
        [rows[i+1][j]/rows[i][j]-1 for j in range(len(symbols))]
        for i in range(len(rows)-1)
    ]
    per_asset = [[row[j] for row in daily] for j in range(len(symbols))]
    fee_fraction = (fee_bps + slippage_bps) / 10000.0

    def fresh_state() -> dict[str, Any]:
        return {
            "holdings": [0.0]*len(symbols), "cash": 1.0,
            "equity": 1.0, "peak": 1.0, "daily_returns": [],
            "equity_curve": [], "fees": 0.0, "turnover": 0.0,
            "rebalance_count": 0,
        }
    states = {
        "selected": fresh_state(),
        "ridge_only": fresh_state(),
        "lightgbm_only": fresh_state(),
        "naive_trailing_vol_risk": fresh_state(),
        "equal_weight_buy_and_hold": fresh_state(),
    }
    history: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    model_snapshot: dict[str, Any] = {}
    previous_champion = "trailing_vol"
    champion_counts: Counter[str] = Counter()
    model_switches = 0

    for t in range(first_exec, len(dates)-1):
        signal = t-1
        before = {name: state["equity"] for name, state in states.items()}
        if (t-first_exec) % rebalance_days == 0:
            validated = score_matured_forecasts(
                history, per_asset,
                current_signal_index=signal,
                forecast_days=forecast_days,
                window_rebalances=validation_window,
                minimum_rebalances=minimum_validated_rebalances,
            )
            decision = choose_tournament_weights(
                validated, mode=mode,
                previous_champion=previous_champion,
                switch_margin=switch_margin,
            )
            if validated["latest_label_end_price_index"] is not None and (
                validated["latest_label_end_price_index"] > signal
            ):
                raise ValueError("Non-mature label used by model tournament")

            forecasts: dict[str, list[float]] = {
                key: [] for key in CANDIDATES
            }
            trained: dict[str, dict[str, Any]] = {}
            for j, symbol in enumerate(symbols):
                feats = _features(per_asset[j], signal, feature_days)
                ridge = _train_asset_model(
                    per_asset[j], signal, feature_days,
                    forecast_days, minimum_training_samples,
                )
                gbm = train_lightgbm_risk(
                    per_asset[j], signal, feature_days,
                    forecast_days, minimum_training_samples,
                )
                trained[symbol] = {"ridge": ridge, "lightgbm": gbm}
                forecasts["ridge"].append(_forecast(ridge, feats))
                forecasts["lightgbm"].append(predict_lightgbm_risk(gbm, feats))
                forecasts["trailing_vol"].append(
                    _rms(per_asset[j][signal-feature_days:signal])
                )
            model_snapshot = trained

            # The chosen risk forecasts are a convex mix. The hard constraints
            # are checked AFTER blending, not bypassed by any model choice.
            chosen = [
                sum(decision["weights"][name] * forecasts[name][j]
                    for name in CANDIDATES)
                for j in range(len(symbols))
            ]
            trailing = daily[signal-feature_days:signal]
            allocations: dict[str, Any] = {}
            for state_name, forecast in (
                ("selected", chosen),
                ("ridge_only", forecasts["ridge"]),
                ("lightgbm_only", forecasts["lightgbm"]),
                ("naive_trailing_vol_risk", forecasts["trailing_vol"]),
            ):
                state = states[state_name]
                allocation = _select_allocation(
                    symbols, forecast, trailing,
                    maximum_name_weight=maximum_name_weight,
                    target_annual_volatility=target_annual_volatility,
                    maximum_gross_exposure=maximum_gross_exposure,
                    uniform_stress_decline=uniform_stress_decline,
                    maximum_uniform_stress_loss=maximum_uniform_stress_loss,
                    maximum_daily_tail_loss=maximum_daily_tail_loss,
                    account_drawdown=state["equity"]/state["peak"] - 1,
                    account_drawdown_trigger=account_drawdown_trigger,
                    drawdown_exposure_cap=drawdown_exposure_cap,
                )
                holdings, cash, loan, paid, turnover = _trade_to_risk_budget(
                    state["holdings"], state["equity"],
                    [allocation["risky_sleeve_weights"][s] for s in symbols],
                    allocation["risky_gross"], fee_fraction,
                )
                if loan != 0:
                    raise ValueError("Model tournament must never create financing debt")
                state["holdings"], state["cash"] = holdings, cash
                state["equity"] -= paid
                state["fees"] += paid
                state["turnover"] += turnover
                state["rebalance_count"] += 1
                allocations[state_name] = allocation

            if t == first_exec:
                bench = states["equal_weight_buy_and_hold"]
                holdings, cash, loan, paid, turnover = _trade_to_risk_budget(
                    bench["holdings"], bench["equity"],
                    [1.0/len(symbols)] * len(symbols), 1.0, fee_fraction,
                )
                if loan != 0:
                    raise ValueError("Buy/hold benchmark cannot borrow")
                bench["holdings"], bench["cash"] = holdings, cash
                bench["equity"] -= paid
                bench["fees"] += paid
                bench["turnover"] += turnover
                bench["rebalance_count"] += 1

            # Construct history AFTER selection; future truths are not stored
            # here. The evaluator computes truths only once fully matured.
            history.append({
                "signal_index": signal,
                "forecasts": forecasts,
            })
            if decision["champion"] != previous_champion:
                model_switches += 1
            previous_champion = decision["champion"]
            champion_counts[decision["champion"]] += 1
            decisions.append({
                "signal_date": dates[signal].isoformat(),
                "execution_date": dates[t].isoformat(),
                "first_held_return_date": dates[t+1].isoformat(),
                "last_completed_validation_signal_date": (
                    dates[validated["last_completed_signal_index"]].isoformat()
                    if validated["last_completed_signal_index"] is not None else None
                ),
                "latest_validation_target_end_date": (
                    dates[validated["latest_label_end_price_index"]].isoformat()
                    if validated["latest_label_end_price_index"] is not None else None
                ),
                "validated_historical_prediction_dates":
                    validated["completed_forecast_dates"],
                "validated_historical_asset_forecasts":
                    validated["completed_asset_predictions"],
                "validation_mae_by_model": validated["mae_by_model"],
                "selection_reason": decision["reason"],
                "selected_champion": decision["champion"],
                "selection_weights": decision["weights"],
                "candidate_forecasts_annual_rms": {
                    name: dict(zip(symbols, forecasts[name])) for name in CANDIDATES
                },
                "selected_forecasts_annual_rms": dict(zip(symbols, chosen)),
                "training_last_label_date": dates[signal].isoformat(),
                "model_training_samples_per_asset":
                    trained[symbols[0]]["ridge"]["training_samples"],
                "selected_allocation": allocations["selected"],
                "ridge_allocation": allocations["ridge_only"],
                "lightgbm_allocation": allocations["lightgbm_only"],
                "naive_allocation": allocations["naive_trailing_vol_risk"],
                "fee_plus_slippage_bps_assumption": fee_bps+slippage_bps,
                "is_real_or_paper_broker_order": False,
            })

        daily_move = daily[t]
        for name, state in states.items():
            state["holdings"] = [
                holding * (1 + ret) for holding, ret in
                zip(state["holdings"], daily_move)
            ]
            state["equity"] = sum(state["holdings"]) + state["cash"]
            if state["equity"] <= 0 or not isfinite(state["equity"]):
                raise ValueError("Historical simulation equity depleted")
            if abs(sum(state["holdings"])+state["cash"]-state["equity"]) > 1e-10:
                raise ValueError("Cash and holdings ledger failed to reconcile")
            state["peak"] = max(state["peak"], state["equity"])
            state["daily_returns"].append(state["equity"]/before[name] - 1)
            state["equity_curve"].append({
                "date": dates[t+1].isoformat(),
                "equity_index": state["equity"],
                "drawdown": state["equity"]/state["peak"]-1,
                "cash_equity_index_units": state["cash"],
                "gross_holdings_equity_index_units": sum(state["holdings"]),
            })

    performance = {}
    for name, state in states.items():
        performance[name] = {
            **_metrics(
                state["daily_returns"], state["equity_curve"], state["equity"]
            ),
            "rebalance_count": state["rebalance_count"],
            "total_cost_equity_index_units": state["fees"],
            "cumulative_traded_notional_over_equity": state["turnover"],
        }

    # Report all matured OOS forecasts only after backtest completion; none
    # feeds a historical decision unless matured on that earlier decision.
    final_scores = score_matured_forecasts(
        history, per_asset,
        current_signal_index=len(dates)-1,
        forecast_days=forecast_days,
        window_rebalances=max(len(history), minimum_validated_rebalances),
        minimum_rebalances=minimum_validated_rebalances,
    )
    return {
        "method": "atlaspm_offline_prequential_risk_model_tournament",
        "status": "OFFLINE_TRAINED_RESEARCH_ONLY",
        "model_family": "ridge_lightgbm_trailing_risk_tournament",
        "selected_model_family": mode,
        "is_trained_machine_learning_model": True,
        "is_official_qlib_pipeline": False,
        "is_pretrained_institutional_model": False,
        "model_validated_for_live_trading": False,
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "trading_mode": "offline_historical_0_to_1x_cash_only",
        "data": {
            "symbols": symbols,
            "first_price_date": dates[0].isoformat(),
            "last_price_date": dates[-1].isoformat(),
            "shared_price_dates": len(dates),
            "first_execution_date": dates[first_exec].isoformat(),
            "held_out_trading_sessions": len(states["selected"]["daily_returns"]),
            "feature_days": feature_days,
            "forecast_days": forecast_days,
            "minimum_training_samples": minimum_training_samples,
            "rebalance_days": rebalance_days,
            "input_price_file": str(path),
            "data_provider_independently_verified": False,
        },
        "selection_policy": {
            "name": POLICY_NAME,
            "mode": mode,
            "window_completed_rebalance_dates": validation_window,
            "minimum_completed_rebalance_dates": minimum_validated_rebalances,
            "switch_and_improvement_margin": switch_margin,
            "cold_start_fallback": "trailing_vol",
            "candidate_models": list(CANDIDATES),
            "scoring_metric": "mean_absolute_error_of_forward_annualized_rms",
            "scoring_uses_only_matured_labels": True,
            "has_independently_untouched_final_test_set": False,
        },
        "risk_policy": {
            "target_annual_volatility": target_annual_volatility,
            "maximum_name_weight": maximum_name_weight,
            "maximum_gross_exposure": maximum_gross_exposure,
            "uniform_stress_decline": uniform_stress_decline,
            "maximum_uniform_stress_loss": maximum_uniform_stress_loss,
            "maximum_daily_tail_loss": maximum_daily_tail_loss,
            "account_drawdown_trigger": account_drawdown_trigger,
            "drawdown_exposure_cap": drawdown_exposure_cap,
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
        },
        "performance": performance,
        "model_selection": {
            "selection_count_by_champion": {
                name: champion_counts[name] for name in CANDIDATES
            },
            "switch_count": model_switches,
            "rebalances_without_completed_validation": sum(
                d["selection_reason"] ==
                "INSUFFICIENT_COMPLETED_VALIDATION_FALLBACK"
                for d in decisions
            ),
            "last_champion": previous_champion,
            "last_selection_weights": (
                decisions[-1]["selection_weights"] if decisions else None
            ),
        },
        "forecast_validation": {
            "out_of_sample_asset_forecast_count":
                final_scores["completed_asset_predictions"],
            "matured_forecast_date_count":
                final_scores["completed_forecast_dates"],
            "mae_by_model": final_scores["mae_by_model"],
            "warning": (
                "Forward risk labels are fully matured before use in each "
                "historical selection; overlapping labels are correlated. "
                "Full-sample MAEs are reporting-only, never used to choose "
                "earlier weights. No untouched final holdout is certified."
            ),
        },
        "final_trained_model_snapshot": model_snapshot,
        "rebalances": decisions,
        "equity_curves": {
            name: state["equity_curve"] for name, state in states.items()
        },
        "warnings": [
            "Tournament selects the best historical risk forecast by mature rolling MAE, not best final return or Sharpe; historical winners can lose money.",
            "No hindsight selection: only forward-labels ending on or before each prior signal date can score candidate models.",
            "A naive past-volatility baseline can win or take full fallback when ML improvement is insufficient.",
            "All selected or blended predictions pass the SAME hard volatility, ES, shock, exposure and drawdown guards as standalone candidates.",
            "Real market histories may contain vendor revisions, survivorship bias, non-tradable fund marks, market impact and omitted cash yields.",
            "Prequential tests are repeated historical model development; a separately locked final test and live paper test are still required.",
            "No financing, IBKR brokerage connection, paper/live orders or validated executable investment advice.",
        ],
    }


def redacted_tournament_console(result: dict[str, Any], output: str) -> dict[str, Any]:
    private = (Path.cwd() / "local_data").resolve()
    target = Path(output).expanduser().resolve()
    try:
        path = target.relative_to(private)
    except ValueError:
        raise ValueError("Tournament output must be inside local_data/") from None
    return {
        "written": str(Path("local_data") / path),
        "method": result["method"],
        "status": result["status"],
        "mode": result["selection_policy"]["mode"],
        "asset_count": len(result["data"]["symbols"]),
        "held_out_trading_sessions":
            result["data"]["held_out_trading_sessions"],
        "rebalances": len(result["rebalances"]),
        "matured_forecast_dates":
            result["forecast_validation"]["matured_forecast_date_count"],
        "selection_count_by_champion":
            result["model_selection"]["selection_count_by_champion"],
        "switch_count": result["model_selection"]["switch_count"],
        "last_champion": result["model_selection"]["last_champion"],
        "model_validated_for_live_trading": False,
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "All trained model trees, forecasts and exact holdings stay in private local_data.",
            "Offline historic 0–1x portfolio only; not a validated model or a live order service.",
        ],
    }
