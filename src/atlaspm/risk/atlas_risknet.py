"""Atlas RiskNet v2: fully independent, from-scratch SINGLE-head risk network.

Not an off-the-shelf model, not a pretrained checkpoint, not a disabled
return-head shortcut. The model is fitted to one target: next-H-day log annual
RMS daily returns. Three independently trained risk experts share a learned
softmax gate. Robust asymmetric Huber loss penalizes large errors and assigns
more weight to *understating* future risk. Research only; no alpha prediction.
"""

from __future__ import annotations

from math import exp, isfinite, log, sqrt
from statistics import fmean
from typing import Any

from atlaspm.ml_dynamic_rebalance import (
    SIGMA_CEILING, SIGMA_FLOOR, _integer, _rms,
)
from atlaspm.atlas_regimenet import (
    BASE_FEATURES, REGIMES, _numpy, _softmax, native_feature_vector,
)

RISKNET_NAME = "atlaspm_original_risknet_v2_single_risk_head"
RISKNET_EPOCHS = 40
RISKNET_SEED = 242762
RISKNET_LEARNING_RATE = 0.03
RISKNET_PENALTY = .0003
RISKNET_UNDERPREDICTION_PENALTY = .4
RISKNET_HUBER_WIDTH = 1.0


def _risk_forward(np, x, weights):
    gate_w, gate_b, expert_w, expert_b = weights
    gate = _softmax(np, x @ gate_w + gate_b)
    experts = x @ expert_w.T + expert_b
    combined = np.sum(gate * experts, axis=1)
    return gate, experts, combined


def _risk_loss(np, predictions, target):
    error = predictions - target
    delta = np.abs(error)
    huber = np.where(
        delta <= RISKNET_HUBER_WIDTH, .5*error*error,
        RISKNET_HUBER_WIDTH*(delta-.5*RISKNET_HUBER_WIDTH),
    )
    asymmetry = 1.0 + RISKNET_UNDERPREDICTION_PENALTY*(error < 0)
    return float(np.mean(asymmetry * huber))


def fit_atlas_risknet(
    by_asset: list[list[float]],
    signal_index: int,
    lookback: int,
    horizon: int,
    minimum_samples: int,
    *,
    train_window: int = 504,
    sample_stride: int = 2,
    epochs: int = RISKNET_EPOCHS,
) -> dict[str, Any]:
    """Train only on fully observed previous H-day risk labels.

    Any sample with forecast origin i uses returns [i,i+horizon), and is
    only eligible at a later signal >=i+horizon. No cross-sample lookahead.
    """
    np = _numpy()
    lookback = _integer(lookback, "lookback", 5, 252)
    horizon = _integer(horizon, "horizon", 2, 63)
    minimum_samples = _integer(minimum_samples, "minimum_samples", 20, 5000)
    train_window = _integer(train_window, "train_window", 60, 5040)
    sample_stride = _integer(sample_stride, "sample_stride", 1, 21)
    epochs = _integer(epochs, "epochs", 1, 200)
    if not (2 <= len(by_asset) <= 12):
        raise ValueError("Atlas RiskNet needs between 2 and 12 assets")
    if any(len(series) < signal_index for series in by_asset):
        raise ValueError("Atlas RiskNet signal beyond available history")
    latest = signal_index-horizon
    eligible = latest-lookback+1
    if eligible < minimum_samples:
        raise ValueError("Atlas RiskNet lacks fully matured training labels")
    start = max(lookback, latest-train_window+1)
    times = list(range(start, latest+1, sample_stride))
    if times and times[-1] != latest:
        times.append(latest)
    if len(times) < 20:
        raise ValueError("Atlas RiskNet has insufficient training timestamps")

    features = []
    targets = []
    for index in times:
        for j, series in enumerate(by_asset):
            features.append(native_feature_vector(by_asset,index,j,lookback))
            targets.append(log(max(SIGMA_FLOOR,_rms(series[index:index+horizon]))))
    x = np.asarray(features, dtype=np.float64)
    y = np.asarray(targets, dtype=np.float64)
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        raise ValueError("Atlas RiskNet encountered non-finite training data")
    feature_means = np.mean(x, axis=0)
    feature_scales = np.maximum(np.std(x, axis=0), 1e-5)
    z = np.clip((x-feature_means)/feature_scales,-4.0,4.0)
    target_mean = float(np.mean(y))
    target_scale = max(float(np.std(y)), .02)
    labels = np.clip((y-target_mean)/target_scale,-6.0,6.0)

    rng = np.random.default_rng(RISKNET_SEED)
    width = z.shape[1]
    weights = [
        rng.normal(0,.035,(width,REGIMES)),
        np.array([.06,0.0,-.06], dtype=np.float64),
        rng.normal(0,.035,(REGIMES,width)),
        np.zeros(REGIMES, dtype=np.float64),
    ]
    first = _risk_loss(np,_risk_forward(np,z,weights)[2],labels)
    best = first
    best_weights = [w.copy() for w in weights]

    for _ in range(epochs):
        gates, experts, pred = _risk_forward(np,z,weights)
        error = pred-labels
        derivative = (
            np.clip(error,-RISKNET_HUBER_WIDTH,RISKNET_HUBER_WIDTH)
            * (1.0+RISKNET_UNDERPREDICTION_PENALTY*(error < 0))
        )/len(z)
        expert_d = gates*derivative[:,None]
        grad_exp_w = expert_d.T @ z
        grad_exp_b = np.sum(expert_d,axis=0)
        grad_gate = derivative[:,None]*experts
        grad_logits = gates*(grad_gate-np.sum(gates*grad_gate,axis=1,keepdims=True))
        grad_gate_w = z.T @ grad_logits
        grad_gate_b = np.sum(grad_logits,axis=0)
        grads = [grad_gate_w,grad_gate_b,grad_exp_w,grad_exp_b]
        for idx in (0,2):
            grads[idx] += RISKNET_PENALTY*weights[idx]
        magnitude = sqrt(sum(float(np.sum(g*g)) for g in grads))
        if not isfinite(magnitude):
            raise ValueError("Atlas RiskNet nonfinite gradient")
        rate = RISKNET_LEARNING_RATE*min(1.0,3.0/max(magnitude,1e-12))
        for idx in range(len(weights)):
            weights[idx] -= rate*grads[idx]
        current = _risk_loss(np,_risk_forward(np,z,weights)[2],labels)
        if not isfinite(current):
            raise ValueError("Atlas RiskNet nonfinite objective")
        if current < best:
            best = current
            best_weights = [w.copy() for w in weights]
    if best >= first:
        raise ValueError("Atlas RiskNet failed to fit a useful training objective")
    return {
        "model": RISKNET_NAME,
        "architecture": "three_expert_softmax_gated_single_log_rms_risk_output",
        "trained_from_scratch": True,
        "pretrained_weights_used": False,
        "return_prediction_head_exists": False,
        "training_objective": "asymmetric_huber_log_annualized_forward_rms",
        "training_time_first_signal_index": times[0],
        "training_time_last_signal_index": times[-1],
        "label_fully_observed_through_price_index":times[-1]+horizon,
        "last_eligible_training_signal_index":latest,
        "fully_matured_historical_training_dates":eligible,
        "subsampled_training_dates":len(times),
        "training_asset_rows":len(x),
        "lookback":lookback,
        "horizon":horizon,
        "regimes":REGIMES,
        "feature_names":list(BASE_FEATURES)+[
            f"asset_identity_{i}" for i in range(len(by_asset))
        ],
        "targets":["log_annualized_forward_root_mean_square_return"],
        "training_epochs":epochs,
        "initial_data_loss":first,
        "final_data_loss":best,
        "feature_means":feature_means.tolist(),
        "feature_scales":feature_scales.tolist(),
        "target_mean":target_mean,
        "target_scale":target_scale,
        "gate_weights":best_weights[0].tolist(),
        "gate_bias":best_weights[1].tolist(),
        "expert_weights":best_weights[2].tolist(),
        "expert_bias":best_weights[3].tolist(),
        "model_validated_for_live_trading":False,
    }


def predict_atlas_risknet(
    trained: dict[str, Any], features: list[float],
) -> dict[str, Any]:
    """Predict one numeric annualized risk and unlabelled expert weights."""
    np = _numpy()
    if trained.get("model") != RISKNET_NAME:
        raise ValueError("Invalid Atlas RiskNet model type")
    if len(features) != len(trained["feature_means"]) or any(
        not isinstance(value,(int,float)) or isinstance(value,bool)
        or not isfinite(value) for value in features
    ):
        raise ValueError("Atlas RiskNet invalid inference inputs")
    z = np.clip(
        (np.asarray([features],dtype=np.float64)-np.asarray(trained["feature_means"]))
        /np.asarray(trained["feature_scales"]),-4,4
    )
    weights = [
        np.asarray(trained["gate_weights"],dtype=np.float64),
        np.asarray(trained["gate_bias"],dtype=np.float64),
        np.asarray(trained["expert_weights"],dtype=np.float64),
        np.asarray(trained["expert_bias"],dtype=np.float64),
    ]
    gates,_,pred = _risk_forward(np,z,weights)
    risk = float(np.clip(
        exp(max(-20.0,min(5.0,float(pred[0]*trained["target_scale"]+
                                     trained["target_mean"])))),
        SIGMA_FLOOR,SIGMA_CEILING
    ))
    probabilities = [float(p) for p in gates[0]]
    if not isfinite(risk) or abs(sum(probabilities)-1)>1e-8:
        raise ValueError("Atlas RiskNet invalid predicted output")
    return {
        "annualized_rms_risk_forecast":risk,
        "annualized_arithmetic_return_forecast":None,
        "latent_expert_probabilities":probabilities,
        "note":"Risk-only model: there is NO predicted investment return/alpha.",
    }
