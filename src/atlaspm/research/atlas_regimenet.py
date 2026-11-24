"""AtlasPM original trainable RegimeNet v1: research-only dual-head risk/return model.

This implements a locally designed gated mixture of three asset-aware experts,
jointly trained to predict future annualized realized RMS risk and arithmetic
return. The gating network and expert weights are fitted with explicit NumPy
backpropagation. Neither existing Ridge/LightGBM/Qlib models nor pretrained
weights are used. Standard softmax/gradient-descent math is not proprietary or
a claim of novel academic invention.

Every input and both supervised targets are time-indexed. A target ending
after the historical decision is never used. Automatic portfolio rebalances
are cash-only 0-1x, subject to independent hard risk caps, and never send
orders. Adjusted close executions and future prices are research assumptions.
"""

from __future__ import annotations

from math import exp, isfinite, log, sqrt
from pathlib import Path
from statistics import fmean
from typing import Any

from atlaspm.engine.backtest import _metrics
from atlaspm.data.historical import TRADING_DAYS_PER_YEAR, load_adjusted_prices
from atlaspm.research.ml_dynamic_rebalance import (
    SIGMA_CEILING, SIGMA_FLOOR, _integer, _real, _rms, _select_allocation,
)
from atlaspm.risk.risk_backtest import _trade_to_risk_budget
from atlaspm.execution.atlas_execution_policy import assess_native_no_trade

MODEL_NAME = "atlaspm_original_regimenet_v1_dual_head"
REGIMES = 3
TRAINING_EPOCHS = 40
LEARNING_RATE = 0.03
RIDGE_PENALTY = 0.0003
DOWNSIDE_RISK_PENALTY = 0.4
BASE_FEATURES = (
    "asset_last_daily_return", "asset_mean_last_5",
    "asset_mean_lookback", "asset_rms_last_5_annualized",
    "asset_rms_lookback_annualized", "asset_downside_rms_annualized",
    "basket_mean_daily_return_lookback",
    "basket_mean_rms_lookback_annualized",
)


def _numpy():
    try:
        import numpy as np
    except (ImportError, OSError) as exc:
        raise ValueError(
            "Atlas native training needs NumPy: python -m pip install -e '.[native]'"
        ) from exc
    return np


def native_feature_vector(
    by_asset: list[list[float]], index: int, asset: int, lookback: int,
) -> list[float]:
    """Features from returns ending at close(index), never after that close."""
    if not by_asset or asset not in range(len(by_asset)):
        raise ValueError("Invalid native feature asset")
    if index < lookback or any(index > len(row) for row in by_asset):
        raise ValueError("Not enough past-only data for native feature vector")
    row = by_asset[asset][index-lookback:index]
    short = row[-5:]
    downside = [min(x, 0.0) for x in row]
    market_window = [a[index-lookback:index] for a in by_asset]
    result = [
        row[-1], fmean(short), fmean(row),
        _rms(short), _rms(row), _rms(downside),
        fmean(fmean(r) for r in market_window),
        fmean(_rms(r) for r in market_window),
    ]
    result += [1.0 if asset == j else 0.0 for j in range(len(by_asset))]
    if not all(isfinite(value) for value in result):
        raise ValueError("Non-finite native history feature")
    return result


def _softmax(np, values):
    shifted = values - np.max(values, axis=1, keepdims=True)
    unnorm = np.exp(shifted)
    return unnorm / np.sum(unnorm, axis=1, keepdims=True)


def _forward(np, normalized, weights):
    gate_w, gate_b, expert_w, expert_b = weights
    gate = _softmax(np, normalized @ gate_w + gate_b)
    experts = np.einsum("nd,kdo->nko", normalized, expert_w) + expert_b
    output = np.einsum("nk,nko->no", gate, experts)
    return gate, experts, output


def _training_loss(np, predicted, target):
    diff = predicted - target
    # The first output is log annual risk; under-predicting observed risk is
    # penalized more than over-predicting equally sized residuals.
    return float(np.mean(
        diff[:, 0]**2 + 0.5*diff[:, 1]**2
        + DOWNSIDE_RISK_PENALTY*np.minimum(diff[:, 0], 0.0)**2
    ))


def fit_atlas_regimenet(
    by_asset: list[list[float]], signal_index: int,
    lookback: int, horizon: int, minimum_samples: int,
    *,
    train_window: int = 504, sample_stride: int = 2,
    epochs: int = TRAINING_EPOCHS,
) -> dict[str, Any]:
    """Original shared multiasset network; no sample label ends after signal.

    Eligible daily signals run from lookback through (signal-horizon). The
    latest `train_window` eligible price indices are candidates; sample_stride
    subsamples them for speed and to reduce overlapping-label dependence.
    """
    np = _numpy()
    lookback = _integer(lookback, "lookback", 5, 252)
    horizon = _integer(horizon, "horizon", 2, 63)
    minimum_samples = _integer(minimum_samples, "minimum_samples", 20, 5000)
    train_window = _integer(train_window, "train_window", 60, 5040)
    sample_stride = _integer(sample_stride, "sample_stride", 1, 21)
    epochs = _integer(epochs, "epochs", 1, 200)
    if not (2 <= len(by_asset) <= 12):
        raise ValueError("Original Atlas RegimeNet needs 2 to 12 assets")
    if any(len(row) < signal_index for row in by_asset):
        raise ValueError("Signal beyond available historical prices")
    latest = signal_index - horizon
    eligible_count = latest - lookback + 1
    if eligible_count < minimum_samples:
        raise ValueError("Insufficient fully matured native training labels")
    start = max(lookback, latest - train_window + 1)
    times = list(range(start, latest + 1, sample_stride))
    if times and times[-1] != latest:
        times.append(latest)
    if len(times) < 20:
        raise ValueError("Too few distinct training timestamps")
    features: list[list[float]] = []
    labels: list[list[float]] = []
    for i in times:
        for j, returns in enumerate(by_asset):
            features.append(native_feature_vector(by_asset, i, j, lookback))
            future = returns[i:i+horizon]
            labels.append([
                float(log(max(SIGMA_FLOOR, _rms(future)))),
                float(fmean(future) * TRADING_DAYS_PER_YEAR),
            ])
    x = np.asarray(features, dtype=np.float64)
    y = np.asarray(labels, dtype=np.float64)
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("Invalid data in Atlas RegimeNet training")
    x_mean = np.mean(x, axis=0)
    x_std = np.maximum(np.std(x, axis=0), 1e-5)
    z = np.clip((x-x_mean)/x_std, -4.0, 4.0)
    y_mean = np.mean(y, axis=0)
    y_std = np.maximum(np.std(y, axis=0), .02)
    targets = np.clip((y-y_mean)/y_std, -6.0, 6.0)
    dimension = z.shape[1]
    random = np.random.default_rng(240109)
    weights = [
        random.normal(0, .035, (dimension, REGIMES)),
        np.array([.06, 0.0, -.06], dtype=np.float64),
        random.normal(0, .035, (REGIMES, dimension, 2)),
        np.zeros((REGIMES, 2), dtype=np.float64),
    ]
    first = _training_loss(np, _forward(np, z, weights)[2], targets)
    best = first
    best_weights = [a.copy() for a in weights]
    for _ in range(epochs):
        gates, experts, prediction = _forward(np, z, weights)
        delta = prediction - targets
        n = len(z)
        dloss = np.stack([
            2*(delta[:, 0] + DOWNSIDE_RISK_PENALTY *
               np.minimum(delta[:, 0], 0.0))/n,
            delta[:, 1]/n,
        ], axis=1)
        expert_derivative = gates[:, :, None] * dloss[:, None, :]
        d_expert_w = np.einsum("nd,nko->kdo", z, expert_derivative)
        d_expert_b = np.sum(expert_derivative, axis=0)
        d_gates = np.einsum("no,nko->nk", dloss, experts)
        d_logits = gates * (d_gates - np.sum(
            gates*d_gates, axis=1, keepdims=True
        ))
        d_gate_w = z.T @ d_logits
        d_gate_b = np.sum(d_logits, axis=0)
        grads = [d_gate_w, d_gate_b, d_expert_w, d_expert_b]
        penalty_norm = 0.0
        for k in (0, 2):
            grads[k] += RIDGE_PENALTY * weights[k]
            penalty_norm += float(np.sum(weights[k]**2))
        grad_norm = sqrt(sum(float(np.sum(g*g)) for g in grads))
        if not isfinite(grad_norm):
            raise ValueError("Non-finite Atlas RegimeNet training gradient")
        step = LEARNING_RATE*min(1.0, 3.0/max(grad_norm, 1e-12))
        for k in range(len(weights)):
            weights[k] -= step*grads[k]
        current = _training_loss(np, _forward(np, z, weights)[2], targets)
        if not isfinite(current):
            raise ValueError("Non-finite Atlas RegimeNet training loss")
        if current < best:
            best = current
            best_weights = [a.copy() for a in weights]
    if best >= first:
        raise ValueError("Atlas RegimeNet failed to improve its training objective")
    return {
        "model": MODEL_NAME,
        "architecture": "shared_three_expert_softmax_gated_dual_risk_return_heads",
        "trained_from_scratch": True,
        "pretrained_weights_used": False,
        "standard_ml_primitives_used": ["softmax", "supervised_gradient_descent"],
        "training_time_first_signal_index": times[0],
        "training_time_last_signal_index": times[-1],
        "label_fully_observed_through_price_index": times[-1] + horizon,
        "last_eligible_training_signal_index": latest,
        "fully_matured_historical_training_dates": eligible_count,
        "subsampled_training_dates": len(times),
        "training_asset_rows": len(x),
        "lookback": lookback,
        "horizon": horizon,
        "regimes": REGIMES,
        "feature_names": list(BASE_FEATURES)+[
            f"asset_identity_{i}" for i in range(len(by_asset))
        ],
        "targets": [
            "log_annualized_forward_root_mean_square_return",
            "annualized_forward_average_daily_arithmetic_return",
        ],
        "training_epochs": epochs,
        "initial_data_loss": first,
        "final_data_loss": best,
        "feature_means": x_mean.tolist(),
        "feature_scales": x_std.tolist(),
        "target_means": y_mean.tolist(),
        "target_scales": y_std.tolist(),
        "gate_weights": best_weights[0].tolist(),
        "gate_bias": best_weights[1].tolist(),
        "expert_weights": best_weights[2].tolist(),
        "expert_bias": best_weights[3].tolist(),
        "model_validated_for_live_trading": False,
    }


def predict_atlas_regimenet(
    trained: dict[str, Any], features: list[float],
) -> dict[str, Any]:
    np = _numpy()
    if trained.get("model") != MODEL_NAME:
        raise ValueError("Wrong original AtlasPM model family")
    if len(features) != len(trained["feature_means"]) or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not isfinite(value) for value in features
    ):
        raise ValueError("Invalid Atlas RegimeNet inference features")
    z = np.clip(
        (np.asarray([features], dtype=np.float64) -
         np.asarray(trained["feature_means"])) /
        np.asarray(trained["feature_scales"]), -4, 4,
    )
    weights = [
        np.asarray(trained["gate_weights"]),
        np.asarray(trained["gate_bias"]),
        np.asarray(trained["expert_weights"]),
        np.asarray(trained["expert_bias"]),
    ]
    gates, _, prediction = _forward(np, z, weights)
    decoded = (
        prediction[0] * np.asarray(trained["target_scales"])
        + np.asarray(trained["target_means"])
    )
    risk = float(np.clip(np.exp(np.clip(decoded[0], -20, 5)),
                         SIGMA_FLOOR, SIGMA_CEILING))
    annual_return = float(np.clip(decoded[1], -2.0, 2.0))
    probs = [float(v) for v in gates[0]]
    if (not isfinite(risk) or not isfinite(annual_return)
            or abs(sum(probs)-1) > 1e-8):
        raise ValueError("Invalid original trained model inference")
    return {
        "annualized_rms_risk_forecast": risk,
        "annualized_arithmetic_return_forecast": annual_return,
        "latent_expert_probabilities": probs,
        "note": "Latent experts are not labeled market crash regimes.",
    }


def _capped_native_weights(
    scores: list[float], name_cap: float,
) -> list[float]:
    if not scores or len(scores)*name_cap < 1-1e-10:
        raise ValueError("Infeasible native portfolio concentration cap")
    if any(not isfinite(x) or x <= 0 for x in scores):
        raise ValueError("Invalid native risk-adjusted asset scores")
    remaining = set(range(len(scores)))
    weights = [0.0]*len(scores)
    residue = 1.0
    while remaining:
        total = sum(scores[i] for i in remaining)
        violating = [
            i for i in remaining
            if residue*scores[i]/total > name_cap + 1e-12
        ]
        if not violating:
            for i in remaining:
                weights[i] = residue*scores[i]/total
            break
        for i in violating:
            weights[i] = name_cap
            residue -= name_cap
            remaining.remove(i)
        if residue < -1e-9:
            raise ValueError("Native caps consumed more than 100%")
    if any(x < -1e-10 or x > name_cap+1e-10 for x in weights):
        raise ValueError("Native allocation violates concentration")
    if abs(sum(weights)-1) > 1e-8:
        raise ValueError("Native sleeve weights fail to sum to one")
    return weights


def backtest_atlas_native(
    path: Path,
    *,
    feature_days: int = 20,
    forecast_days: int = 5,
    minimum_training_samples: int = 756,
    training_window: int = 504,
    sample_stride: int = 2,
    training_epochs: int = TRAINING_EPOCHS,
    rebalance_days: int = 21,
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
    no_trade_band: float = 0.0,
    return_head_strength: float = 1.0,
    architecture: str = "dual",
    fusion_allocator_mode: str = "shrinkage",
) -> dict[str, Any]:
    """Train our own network and simulate each next-close rebalance.

    No broker API access, no predicted alpha assurance, and no margin debt.
    """
    feature_days = _integer(feature_days, "feature_days", 5, 252)
    forecast_days = _integer(forecast_days, "forecast_days", 2, 63)
    minimum_training_samples = _integer(
        minimum_training_samples, "minimum_training_samples", 20, 5000
    )
    training_window = _integer(training_window, "training_window", 60, 5040)
    sample_stride = _integer(sample_stride, "sample_stride", 1, 21)
    training_epochs = _integer(training_epochs, "training_epochs", 1, 200)
    rebalance_days = _integer(rebalance_days, "rebalance_days", 1, 252)
    target_annual_volatility = _real(target_annual_volatility, "target_annual_volatility", .001, 2)
    maximum_name_weight = _real(maximum_name_weight, "maximum_name_weight", .05, 1)
    maximum_gross_exposure = _real(maximum_gross_exposure, "maximum_gross_exposure", 0, 1)
    uniform_stress_decline = _real(uniform_stress_decline, "uniform_stress_decline", 0, 1)
    maximum_uniform_stress_loss = _real(maximum_uniform_stress_loss, "maximum_uniform_stress_loss", 0, 1)
    maximum_daily_tail_loss = _real(maximum_daily_tail_loss, "maximum_daily_tail_loss", 0, 1)
    account_drawdown_trigger = _real(account_drawdown_trigger, "account_drawdown_trigger", .001, 1)
    drawdown_exposure_cap = _real(drawdown_exposure_cap, "drawdown_exposure_cap", 0, 1)
    fee_bps = _real(fee_bps, "fee_bps", 0, 1000)
    slippage_bps = _real(slippage_bps, "slippage_bps", 0, 1000)
    no_trade_band = _real(no_trade_band, "no_trade_band", 0, .5)
    return_head_strength = _real(
        return_head_strength, "return_head_strength", 0, 1
    )
    if fusion_allocator_mode not in ("inverse_risk","downside","shrinkage"):
        raise ValueError("Unknown fusion allocator ablation")
    if architecture != "fusion" and fusion_allocator_mode != "shrinkage":
        raise ValueError("Fusion-specific allocator ablations require fusion architecture")
    if architecture not in ("dual","risknet","fusion"):
        raise ValueError("Atlas native architecture must be dual, risknet, or fusion")
    if architecture in ("risknet","fusion") and return_head_strength != 0:
        raise ValueError(
            "Risk-only Atlas model has no return head: --return-head-strength must be 0"
        )
    if architecture == "risknet":
        from atlaspm.risk.atlas_risknet import (
            RISKNET_NAME, fit_atlas_risknet, predict_atlas_risknet,
        )
    if architecture == "fusion":
        from atlaspm.risk.atlas_fusionnet import (
            FUSION_NAME, _downside_rms, fit_atlas_fusionnet,
            fusion_feature_vector, predict_atlas_fusionnet,
        )
        from atlaspm.engine.atlas_fusion_allocator import allocate_fusion_sleeve

    _numpy()
    prices = load_adjusted_prices(path)
    symbols = sorted(prices)
    if not 2 <= len(symbols) <= 12 or len(symbols)*maximum_name_weight < 1-1e-10:
        raise ValueError("Atlas native model requires 2-12 symbols with feasible concentration")
    dates = sorted(set.intersection(*(set(prices[s]) for s in symbols)))
    first_exec = feature_days + forecast_days + minimum_training_samples
    if len(dates) <= first_exec+2:
        raise ValueError("Not enough aligned close dates for native model walk-forward")
    rows = [[prices[s][d] for s in symbols] for d in dates]
    daily = [
        [rows[i+1][j]/rows[i][j]-1 for j in range(len(symbols))]
        for i in range(len(rows)-1)
    ]
    per_asset = [[r[j] for r in daily] for j in range(len(symbols))]
    fee = (fee_bps + slippage_bps)/10000
    no_trade_risk_policy = {
        "target_annual_volatility": target_annual_volatility,
        "maximum_gross_exposure": maximum_gross_exposure,
        "uniform_stress_decline": uniform_stress_decline,
        "maximum_uniform_stress_loss": maximum_uniform_stress_loss,
        "maximum_daily_tail_loss": maximum_daily_tail_loss,
        "account_drawdown_trigger": account_drawdown_trigger,
        "drawdown_exposure_cap": drawdown_exposure_cap,
    }

    def empty():
        return {
            "holdings": [0.0]*len(symbols), "cash": 1.0, "equity": 1.0,
            "peak": 1.0, "returns": [], "curve": [],
            "total_cost": 0.0, "turnover": 0.0, "rebalances": 0,
            "skipped_rebalance_decisions": 0,
        }

    states = {
        "atlas_native": empty(),
        "naive_trailing_vol_risk": empty(),
        "equal_weight_buy_and_hold": empty(),
    }
    decisions = []
    predictions = []
    last_model = {}
    for t in range(first_exec, len(dates)-1):
        signal = t-1
        before = {key: state["equity"] for key, state in states.items()}
        if (t-first_exec) % rebalance_days == 0:
            if architecture == "fusion":
                fitted = fit_atlas_fusionnet(
                    per_asset, signal, feature_days, forecast_days,
                    minimum_training_samples, train_window=training_window,
                    sample_stride=sample_stride, epochs=training_epochs,
                )
                outputs = [
                    predict_atlas_fusionnet(
                        fitted, fusion_feature_vector(
                            per_asset, signal, j, feature_days
                        )
                    ) for j in range(len(symbols))
                ]
            elif architecture == "risknet":
                fitted = fit_atlas_risknet(
                    per_asset, signal, feature_days, forecast_days,
                    minimum_training_samples, train_window=training_window,
                    sample_stride=sample_stride, epochs=training_epochs,
                )
                outputs = [
                    predict_atlas_risknet(
                        fitted, native_feature_vector(
                            per_asset, signal, j, feature_days
                        )
                    ) for j in range(len(symbols))
                ]
            else:
                fitted = fit_atlas_regimenet(
                    per_asset, signal, feature_days, forecast_days,
                    minimum_training_samples, train_window=training_window,
                    sample_stride=sample_stride, epochs=training_epochs,
                )
                outputs = [
                    predict_atlas_regimenet(
                        fitted, native_feature_vector(
                            per_asset, signal, j, feature_days
                        )
                    ) for j in range(len(symbols))
                ]
            last_model = fitted
            vol = [out["annualized_rms_risk_forecast"] for out in outputs]
            original_alpha = [
                out["annualized_arithmetic_return_forecast"] for out in outputs
            ]
            downside = [
                out.get("annualized_downside_rms_risk_forecast")
                for out in outputs
            ]
            # A risk-only network has NO trained return signal. Zero is only a
            # neutral allocation constant, never an inferred return forecast.
            alpha = [0.0 if value is None else value for value in original_alpha]
            naive_vol = [
                _rms(per_asset[j][signal-feature_days:signal])
                for j in range(len(symbols))
            ]
            # In-house, risk-adjusted positive preference, not mean-variance
            # or a packaged optimizer. Relative signals affect sleeve weights.
            trailing = daily[signal-feature_days:signal]
            fusion_allocator = None
            if architecture == "fusion":
                fusion_allocator = allocate_fusion_sleeve(
                    symbols, vol, downside, trailing,
                    max_weight=maximum_name_weight,
                    mode=fusion_allocator_mode,
                )
                native_sleeve = fusion_allocator["weights"]
                weights = [native_sleeve[s] for s in symbols]
            else:
                desirability = [
                    (1/max(v, .03)) * exp(
                        .35*return_head_strength*max(-2.0, min(2.0, a/max(v, .08)))
                    )
                    for v, a in zip(vol, alpha)
                ]
                weights = _capped_native_weights(desirability, maximum_name_weight)
                native_sleeve = dict(zip(symbols, weights))
            expected_sleeve_annual = sum(w*a for w, a in zip(weights, alpha))
            # The return head may only REDUCE an independent risk cap, never
            # increase exposure past a hard broker-independent risk guard.
            return_confidence_cap = max(
                0.0, min(
                    1.0, 1.0 + return_head_strength*expected_sleeve_annual/.15
                )
            )
            allocations = {}
            execution_decisions = {}
            for label, sigma, override, extra_cap in (
                ("atlas_native", vol, native_sleeve, return_confidence_cap),
                ("naive_trailing_vol_risk", naive_vol, None, 1.0),
            ):
                state = states[label]
                allocation = _select_allocation(
                    symbols, sigma, trailing,
                    maximum_name_weight=maximum_name_weight,
                    target_annual_volatility=target_annual_volatility,
                    maximum_gross_exposure=maximum_gross_exposure,
                    uniform_stress_decline=uniform_stress_decline,
                    maximum_uniform_stress_loss=maximum_uniform_stress_loss,
                    maximum_daily_tail_loss=maximum_daily_tail_loss,
                    account_drawdown=state["equity"]/state["peak"]-1,
                    account_drawdown_trigger=account_drawdown_trigger,
                    drawdown_exposure_cap=drawdown_exposure_cap,
                    sleeve_weights=override,
                    additional_exposure_cap=extra_cap,
                )
                no_trade = assess_native_no_trade(
                    symbols=symbols,
                    existing_holdings=state["holdings"],
                    existing_cash=state["cash"],
                    equity=state["equity"],
                    proposed_allocation=allocation,
                    risk_forecasts=sigma,
                    trailing_daily_returns=trailing,
                    no_trade_band=no_trade_band,
                    name_weight_cap=maximum_name_weight,
                    risk_policy=no_trade_risk_policy,
                    account_drawdown=state["equity"]/state["peak"]-1,
                    additional_exposure_cap=extra_cap,
                )
                if no_trade["skipped"]:
                    h, cash, loan, paid, turnover = (
                        state["holdings"], state["cash"], 0, 0.0, 0.0
                    )
                    state["skipped_rebalance_decisions"] += 1
                else:
                    h, cash, loan, paid, turnover = _trade_to_risk_budget(
                        state["holdings"], state["equity"],
                        [allocation["risky_sleeve_weights"][s] for s in symbols],
                        allocation["risky_gross"], fee,
                    )
                    state["rebalances"] += 1
                if loan != 0:
                    raise ValueError("Atlas native engine attempted borrowing")
                state["holdings"], state["cash"] = h, cash
                state["equity"] -= paid
                state["total_cost"] += paid
                state["turnover"] += turnover
                allocations[label] = allocation
                execution_decisions[label] = no_trade
            if t == first_exec:
                bench = states["equal_weight_buy_and_hold"]
                h, cash, loan, paid, turnover = _trade_to_risk_budget(
                    bench["holdings"],bench["equity"],
                    [1/len(symbols)]*len(symbols),1.0,fee,
                )
                if loan != 0:
                    raise ValueError("Buy-and-hold borrowed cash")
                bench["holdings"], bench["cash"] = h, cash
                bench["equity"] -= paid
                bench["total_cost"] += paid
                bench["turnover"] += turnover
                bench["rebalances"] += 1
            for j,symbol in enumerate(symbols):
                # Reporting-only: fully realized labels are NOT used for
                # historical model decisions, only for after-the-fact OOS MAE.
                if signal+forecast_days <= len(daily):
                    observed = per_asset[j][signal:signal+forecast_days]
                    predictions.append({
                        "signal_date": dates[signal].isoformat(),
                        "asset": symbol,
                        "annualized_risk_forecast": vol[j],
                        "annualized_downside_rms_forecast": downside[j],
                        "realized_forward_downside_rms": (
                            _downside_rms(observed) if architecture == "fusion" else None
                        ),
                        "baseline_trailing_risk_forecast": naive_vol[j],
                        "annualized_return_forecast": original_alpha[j],
                        "realized_forward_annual_rms": _rms(observed),
                        "realized_forward_arithmetic_annual_return":
                            fmean(observed)*TRADING_DAYS_PER_YEAR,
                    })
            decisions.append({
                "signal_date": dates[signal].isoformat(),
                "execution_date": dates[t].isoformat(),
                "first_held_return_date": dates[t+1].isoformat(),
                "training_last_eligible_signal_date":
                    dates[fitted["last_eligible_training_signal_index"]].isoformat(),
                "training_last_label_end_date":
                    dates[fitted["label_fully_observed_through_price_index"]].isoformat(),
                "training_asset_rows": fitted["training_asset_rows"],
                "training_initial_loss": fitted["initial_data_loss"],
                "training_final_loss": fitted["final_data_loss"],
                "annualized_risk_forecasts": dict(zip(symbols, vol)),
                "annualized_downside_risk_forecasts": dict(zip(symbols, downside)),
                "fusion_allocator_diagnostics": fusion_allocator,
                "annualized_return_forecasts": dict(zip(symbols, original_alpha)),
                "latent_expert_probabilities": {
                    s: o["latent_expert_probabilities"]
                    for s, o in zip(symbols, outputs)
                },
                "return_confidence_exposure_cap": return_confidence_cap,
                "trained_model_architecture": architecture,
                "native_execution_policy": execution_decisions["atlas_native"],
                "baseline_execution_policy":
                    execution_decisions["naive_trailing_vol_risk"],
                "atlas_native_allocation": allocations["atlas_native"],
                "naive_allocation": allocations["naive_trailing_vol_risk"],
                "is_real_or_paper_broker_order": False,
            })
        changes = daily[t]
        for name,state in states.items():
            state["holdings"] = [
                h*(1+r) for h,r in zip(state["holdings"],changes)
            ]
            state["equity"] = sum(state["holdings"])+state["cash"]
            if state["equity"] <= 0 or not isfinite(state["equity"]):
                raise ValueError("Atlas native cash-only portfolio equity depleted")
            if abs(sum(state["holdings"])+state["cash"]-state["equity"]) > 1e-10:
                raise ValueError("Atlas native holdings and cash fail to reconcile")
            state["peak"] = max(state["peak"],state["equity"])
            state["returns"].append(state["equity"]/before[name]-1)
            state["curve"].append({
                "date": dates[t+1].isoformat(),
                "equity_index": state["equity"],
                "drawdown": state["equity"]/state["peak"]-1,
                "cash_equity_index_units": state["cash"],
                "gross_holdings_equity_index_units": sum(state["holdings"]),
            })
    scores = {}
    for name,state in states.items():
        scores[name] = {
            **_metrics(state["returns"],state["curve"],state["equity"]),
            "rebalance_count": state["rebalances"],
            "total_cost_equity_index_units": state["total_cost"],
            "cumulative_traded_notional_over_equity": state["turnover"],
        }
    return {
        "method": (
            "atlaspm_original_fusionnet_v1_walk_forward" if architecture == "fusion"
            else "atlaspm_original_risknet_v2_walk_forward" if architecture == "risknet"
            else "atlaspm_original_regimenet_v1_walk_forward"
        ),
        "status": "OFFLINE_TRAINED_RESEARCH_ONLY",
        "model_family": (
            FUSION_NAME if architecture == "fusion" else
            RISKNET_NAME if architecture == "risknet" else MODEL_NAME
        ),
        "architecture_choice": architecture,
        "return_prediction_head_trained": architecture == "dual",
        "downside_risk_head_trained": architecture == "fusion",
        "trained_from_scratch": True,
        "pretrained_weights_used": False,
        "is_official_qlib_pipeline": False,
        "data": {
            "symbols": symbols,
            "first_price_date": dates[0].isoformat(),
            "last_price_date": dates[-1].isoformat(),
            "first_execution_date": dates[first_exec].isoformat(),
            "held_out_trading_sessions": len(states["atlas_native"]["returns"]),
            "minimum_training_samples": minimum_training_samples,
            "actual_train_window_dates": training_window,
            "sample_stride": sample_stride,
            "training_epochs": training_epochs,
            "feature_days": feature_days,
            "forecast_days": forecast_days,
            "rebalance_days": rebalance_days,
            "data_provider_independently_verified": False,
        },
        "policy": {
            "maximum_name_weight": maximum_name_weight,
            "maximum_gross_exposure": maximum_gross_exposure,
            "target_annual_volatility": target_annual_volatility,
            "uniform_stress_decline": uniform_stress_decline,
            "maximum_uniform_stress_loss": maximum_uniform_stress_loss,
            "maximum_daily_tail_loss": maximum_daily_tail_loss,
            "account_drawdown_trigger": account_drawdown_trigger,
            "drawdown_exposure_cap": drawdown_exposure_cap,
            "fee_bps": fee_bps, "slippage_bps": slippage_bps,
            "no_trade_band_l1_account_weight": no_trade_band,
            "return_head_strength": return_head_strength,
            "architecture": architecture,
            "fusion_allocator_mode": (
                fusion_allocator_mode if architecture == "fusion" else None
            ),
        },
        "execution_summary": {
            name: {
                "executed_rebalances": state["rebalances"],
                "skipped_rebalance_decisions": state["skipped_rebalance_decisions"],
            } for name, state in states.items()
        },
        "performance": scores,
        "forecast_validation": {
            "out_of_sample_asset_forecast_count": len(predictions),
            "risk_mae_annualized": fmean(
                abs(p["annualized_risk_forecast"] -
                    p["realized_forward_annual_rms"]) for p in predictions
            ) if predictions else None,
            "downside_rms_mae_annualized": fmean(
                abs(p["annualized_downside_rms_forecast"] -
                    p["realized_forward_downside_rms"]) for p in predictions
            ) if predictions and architecture == "fusion" else None,
            "naive_trailing_risk_mae_annualized": fmean(
                abs(p["baseline_trailing_risk_forecast"] -
                    p["realized_forward_annual_rms"]) for p in predictions
            ) if predictions else None,
            "return_mae_annualized": fmean(
                abs(p["annualized_return_forecast"] -
                    p["realized_forward_arithmetic_annual_return"])
                for p in predictions
            ) if predictions and architecture == "dual" else None,
            "zero_return_baseline_mae_annualized": fmean(
                abs(p["realized_forward_arithmetic_annual_return"])
                for p in predictions
            ) if predictions else None,
            "return_forecast_better_than_zero_baseline": (
                fmean(
                    abs(p["annualized_return_forecast"] -
                        p["realized_forward_arithmetic_annual_return"])
                    for p in predictions
                ) < fmean(
                    abs(p["realized_forward_arithmetic_annual_return"])
                    for p in predictions
                ) if predictions and architecture == "dual" else None
            ),
            "warning": "Overlapping prediction labels are correlated; train and test are time-ordered but there is no untouched independent final holdout.",
        },
        "final_trained_model_snapshot": last_model,
        "rebalances": decisions,
        "equity_curves": {
            name: state["curve"] for name,state in states.items()
        },
        "model_validated_for_live_trading": False,
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "This is our own shared softmax-gated three-expert dual-target architecture and trained parameters, not imported pre-trained Ridge/LightGBM/Qlib weights. It uses standard published ML primitives.",
            "Atlas FusionNet is a proposed original multiscale overall/downside-risk network and shrinkage-correlation allocation; 30 external references are research influences, NOT models installed or fitted here.",
            "Atlas RiskNet is separately fitted with a risk-only objective and has no return head; absent forecasts are null, not false alpha predictions.",
            "Return head is optional for allocation ablation, and zero-return MAE baseline is provided; training loss improvement is not evidence of investable alpha.",
            "No-trade bands revalidate actual holdings against all hard risk caps before skipping; fewer trades are not a guaranteed performance gain.",
            "Forecast return is uncertain, not alpha proof; stress and loss budgets cannot be overridden by learned forecasts.",
            "Risk and return targets are only fitted when their horizon is fully observed at each prior decision.",
            "Historical prices, research closing-price execution, cash returns and transaction costs are not independently audited; cash yield is set to zero.",
            "Models are repeatedly trained/tested in this historical simulation; no genuinely untouched final test or paper brokerage integration.",
            "Maximum risky exposure is 1x cash account; IBKR financing and live/paper orders are not enabled.",
        ],
    }


def redacted_atlas_native_console(result: dict[str, Any], output: str) -> dict[str, Any]:
    private = (Path.cwd()/"local_data").resolve()
    target = Path(output).expanduser().resolve()
    try:
        relative = target.relative_to(private)
    except ValueError:
        raise ValueError("Native model report must be private under local_data/") from None
    return {
        "written": str(Path("local_data")/relative),
        "method": result["method"],
        "status": result["status"],
        "model_family": result["model_family"],
        "architecture": result["architecture_choice"],
        "return_prediction_head_trained": result["return_prediction_head_trained"],
        "downside_risk_head_trained": result["downside_risk_head_trained"],
        "trained_from_scratch": result["trained_from_scratch"],
        "asset_count": len(result["data"]["symbols"]),
        "held_out_trading_sessions":
            result["data"]["held_out_trading_sessions"],
        "automatic_simulated_rebalances": len(result["rebalances"]),
        "no_trade_band_l1_account_weight":
            result["policy"]["no_trade_band_l1_account_weight"],
        "return_head_strength": result["policy"]["return_head_strength"],
        "native_skipped_rebalance_decisions":
            result["execution_summary"]["atlas_native"]["skipped_rebalance_decisions"],
        "return_forecast_better_than_zero_baseline":
            result["forecast_validation"]["return_forecast_better_than_zero_baseline"],
        "last_training_asset_rows":
            result["rebalances"][-1]["training_asset_rows"],
        "model_validated_for_live_trading": False,
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Original native model weights/forecasts/returns remain in local_data only.",
            "Offline cash-only research without broker execution.",
        ],
    }
