"""Atlas FusionNet v0.7.0 — ORIGINAL trained dual-RISK-head research prototype.

The self-trained experts predict forward overall RMS and downside RMS only.
Inspired by 30 documented public methods, especially GARCH/HAR multiscale,
asymmetric risk, multi-variate correlation, robust losses, and learned gating.
Does NOT import or claim to implement TFT/DCC/HRP/EVT/CAViaR/Chronos code.

No forward return/alpha head. No broker orders. Fixed features/penalties,
not retrospectively tuned to 2020 losses on the previously studied history.
"""

from __future__ import annotations

from math import exp, isfinite, log, sqrt
from statistics import fmean
from typing import Any

from atlaspm.atlas_regimenet import (
    BASE_FEATURES, REGIMES, _numpy, _softmax, native_feature_vector,
)
from atlaspm.ml_dynamic_rebalance import (
    SIGMA_CEILING, SIGMA_FLOOR, _integer, _rms,
)

FUSION_NAME="atlaspm_original_fusionnet_v1_multiscale_dual_risk"
FUSION_SEED=382271
FUSION_EPOCHS=40
FUSION_RATE=.025
FUSION_REG=.0003
FUSION_HUBER=1.0
RISK_UNDERPENALTY=.4
DOWNSIDE_UNDERPENALTY=.6
DOWNSIDE_LOSS_WEIGHT=.7
DOWNSIDE_MIN_ANNUAL=.01


def _downside_rms(returns: list[float]) -> float:
    if not returns:
        raise ValueError("Downside RMS needs past/future observations")
    return _rms([min(v,0.) for v in returns])


def fusion_feature_vector(
    by_asset: list[list[float]],
    index: int,
    asset: int,
    lookback: int,
) -> list[float]:
    """5 / 10 / L-day history + asset & market past-only cross-asset context."""
    shared=native_feature_vector(by_asset,index,asset,lookback)
    row=by_asset[asset]
    scales=sorted(set((5,min(10,lookback),lookback)))
    extra=[]
    for n in scales:
        samples=row[index-n:index]
        extra.extend([
            fmean(samples),
            _rms(samples),
            _downside_rms(samples),
            max(0.,-min(samples)),
        ])
    windows=[v[index-lookback:index] for v in by_asset]
    if any(len(w)!=lookback for w in windows):
        raise ValueError("Insufficient shared lookback for fusion features")
    basket=[fmean(w[k] for w in windows) for k in range(lookback)]
    asset_window=windows[asset]
    a_mean=fmean(asset_window)
    b_mean=fmean(basket)
    a_var=fmean((x-a_mean)**2 for x in asset_window)
    b_var=fmean((x-b_mean)**2 for x in basket)
    denom=sqrt(max(0.,a_var*b_var))
    cross=(
        fmean(
            (a-a_mean)*(b-b_mean) for a,b in zip(asset_window,basket)
        )/denom if denom>1e-16 else 0.
    )
    common_downshare=fmean(
        1. if sum(w[k]<0 for w in windows)>=
        (len(windows)+1)//2 else 0.
        for k in range(lookback)
    )
    current_shock=(
        fmean(1. if asset_window[k]<0 and basket[k]<0 else 0.
              for k in range(lookback))
    )
    extra.extend([
        max(-1.,min(1.,cross)),
        common_downshare,
        current_shock,
        _downside_rms(basket),
    ])
    result=shared+extra
    if any(not isfinite(v) for v in result):
        raise ValueError("Non-finite FusionNet feature")
    return result


def fusion_feature_names(asset_count: int, lookback: int) -> list[str]:
    scales=sorted(set((5,min(10,lookback),lookback)))
    names=list(BASE_FEATURES)+[
        f"asset_identity_{i}" for i in range(asset_count)
    ]
    for scale in scales:
        names += [
            f"asset_mean_{scale}",f"asset_rms_{scale}",
            f"asset_downside_rms_{scale}",f"asset_worst_day_loss_{scale}",
        ]
    names += [
        "asset_basket_return_correlation_past",
        "market_simultaneous_negative_day_fraction",
        "asset_negative_same_day_as_basket_fraction",
        "basket_downside_rms_past",
    ]
    return names


def _fusion_forward(np,features,weights):
    gate_w,gate_b,expert_w,expert_b=weights
    gate=_softmax(np,features @ gate_w+gate_b)
    experts=np.einsum("nd,kdo->nko",features,expert_w)+expert_b
    prediction=np.einsum("nk,nko->no",gate,experts)
    return gate,experts,prediction


def _fusion_loss_and_derivative(np,prediction,target):
    error=prediction-target
    clipped=np.clip(error,-FUSION_HUBER,FUSION_HUBER)
    absolute=np.abs(error)
    huber=np.where(
        absolute<=FUSION_HUBER,.5*error**2,
        FUSION_HUBER*(absolute-.5*FUSION_HUBER),
    )
    asymmetry=1+np.where(
        error<0,
        np.asarray([RISK_UNDERPENALTY,DOWNSIDE_UNDERPENALTY]),
        0.,
    )
    head_weights=np.asarray([1.,DOWNSIDE_LOSS_WEIGHT])
    loss=float(np.mean(np.sum(huber*asymmetry*head_weights,axis=1)))
    gradient=(
        clipped*asymmetry*head_weights/len(prediction)
    )
    return loss,gradient


def fit_atlas_fusionnet(
    by_asset: list[list[float]],
    signal_index: int,
    lookback: int,
    horizon: int,
    minimum_samples: int,
    *,
    train_window: int=504,
    sample_stride: int=2,
    epochs: int=FUSION_EPOCHS,
) -> dict[str,Any]:
    np=_numpy()
    lookback=_integer(lookback,"lookback",5,252)
    horizon=_integer(horizon,"horizon",2,63)
    minimum_samples=_integer(minimum_samples,"minimum_samples",20,5000)
    train_window=_integer(train_window,"train_window",60,5040)
    sample_stride=_integer(sample_stride,"sample_stride",1,21)
    epochs=_integer(epochs,"epochs",1,200)
    if not 2<=len(by_asset)<=12:
        raise ValueError("FusionNet needs 2-12 aligned daily-price assets")
    if any(len(row)<signal_index for row in by_asset):
        raise ValueError("Insufficient feature data at signal")
    latest=signal_index-horizon
    eligible=latest-lookback+1
    if eligible<minimum_samples:
        raise ValueError("Not enough fully matured FusionNet training targets")
    start=max(lookback,latest-train_window+1)
    timestamps=list(range(start,latest+1,sample_stride))
    if timestamps and timestamps[-1]!=latest:
        timestamps.append(latest)
    if len(timestamps)<20:
        raise ValueError("Too few distinct FusionNet historical samples")
    x,y=[],[]
    for i in timestamps:
        for j,asset_returns in enumerate(by_asset):
            x.append(fusion_feature_vector(by_asset,i,j,lookback))
            future=asset_returns[i:i+horizon]
            y.append([
                log(max(SIGMA_FLOOR,_rms(future))),
                log(max(DOWNSIDE_MIN_ANNUAL,_downside_rms(future))),
            ])
    X=np.asarray(x,dtype=np.float64)
    Y=np.asarray(y,dtype=np.float64)
    if not np.all(np.isfinite(X)) or not np.all(np.isfinite(Y)):
        raise ValueError("Nonfinite FusionNet training inputs")
    mean=np.mean(X,axis=0)
    scale=np.maximum(np.std(X,axis=0),1e-5)
    z=np.clip((X-mean)/scale,-4,4)
    y_mean=np.mean(Y,axis=0)
    y_scale=np.maximum(np.std(Y,axis=0),.02)
    targets=np.clip((Y-y_mean)/y_scale,-6,6)
    width=z.shape[1]
    if width != len(fusion_feature_names(len(by_asset),lookback)):
        raise ValueError("Fusion feature vector/name schema mismatch")
    rng=np.random.default_rng(FUSION_SEED)
    weights=[
        rng.normal(0,.035,(width,REGIMES)),
        np.array([.06,0.,-.06],dtype=np.float64),
        rng.normal(0,.035,(REGIMES,width,2)),
        np.zeros((REGIMES,2),dtype=np.float64),
    ]
    initial,_=_fusion_loss_and_derivative(
        np,_fusion_forward(np,z,weights)[2],targets
    )
    best=initial
    best_weights=[w.copy() for w in weights]
    for _ in range(epochs):
        gate,experts,prediction=_fusion_forward(np,z,weights)
        _,derivative=_fusion_loss_and_derivative(np,prediction,targets)
        delta_expert=gate[:,:,None]*derivative[:,None,:]
        grad_exp_w=np.einsum("nd,nko->kdo",z,delta_expert)
        grad_exp_b=np.sum(delta_expert,axis=0)
        dgate=np.einsum("no,nko->nk",derivative,experts)
        dlogits=gate*(dgate-np.sum(gate*dgate,axis=1,keepdims=True))
        grad_gate_w=z.T@dlogits
        grad_gate_b=np.sum(dlogits,axis=0)
        gradients=[grad_gate_w,grad_gate_b,grad_exp_w,grad_exp_b]
        for k in (0,2):
            gradients[k]+=FUSION_REG*weights[k]
        norm=sqrt(sum(float(np.sum(g*g)) for g in gradients))
        if not isfinite(norm):
            raise ValueError("Nonfinite FusionNet gradient")
        step=FUSION_RATE*min(1.,3./max(norm,1e-12))
        for k in range(len(weights)):
            weights[k]-=step*gradients[k]
        current,_=_fusion_loss_and_derivative(
            np,_fusion_forward(np,z,weights)[2],targets
        )
        if not isfinite(current):
            raise ValueError("Nonfinite FusionNet training objective")
        if current<best:
            best=current
            best_weights=[w.copy() for w in weights]
    if best>=initial:
        raise ValueError("FusionNet failed to improve its training objective")
    return {
        "model":FUSION_NAME,
        "architecture":"scratch_trained_three_gated_expert_two_risk_head_multiscale",
        "inspired_by_public_ideas":[
            "HAR-RV multiscale risk","GJR shock asymmetry",
            "TFT-style gating concept","cross-asset context",
            "robust downside-risk objective",
        ],
        "is_any_original_published_model_implementation":False,
        "trained_from_scratch":True,
        "pretrained_weights_used":False,
        "return_prediction_head_exists":False,
        "training_objective":"asymmetric_huber_joint_log_rms_and_log_downside_rms",
        "training_time_first_signal_index":timestamps[0],
        "training_time_last_signal_index":timestamps[-1],
        "label_fully_observed_through_price_index":timestamps[-1]+horizon,
        "last_eligible_training_signal_index":latest,
        "fully_matured_historical_training_dates":eligible,
        "subsampled_training_dates":len(timestamps),
        "training_asset_rows":len(X),
        "lookback":lookback,
        "horizon":horizon,
        "regimes":REGIMES,
        "feature_names":fusion_feature_names(len(by_asset),lookback),
        "targets":[
            "log_annualized_forward_root_mean_square_return",
            "log_annualized_forward_downside_root_mean_square_return",
        ],
        "training_epochs":epochs,
        "initial_data_loss":initial,
        "final_data_loss":best,
        "feature_means":mean.tolist(),
        "feature_scales":scale.tolist(),
        "target_means":y_mean.tolist(),
        "target_scales":y_scale.tolist(),
        "gate_weights":best_weights[0].tolist(),
        "gate_bias":best_weights[1].tolist(),
        "expert_weights":best_weights[2].tolist(),
        "expert_bias":best_weights[3].tolist(),
        "model_validated_for_live_trading":False,
    }


def predict_atlas_fusionnet(
    trained: dict[str,Any], features: list[float],
) -> dict[str,Any]:
    np=_numpy()
    if trained.get("model")!=FUSION_NAME:
        raise ValueError("Wrong Atlas FusionNet model family")
    if len(features)!=len(trained["feature_means"]) or any(
        isinstance(v,bool) or not isinstance(v,(int,float))
        or not isfinite(v) for v in features
    ):
        raise ValueError("Invalid FusionNet inference history")
    z=np.clip(
        (np.asarray([features],dtype=np.float64)
         -np.asarray(trained["feature_means"],dtype=np.float64))
        /np.asarray(trained["feature_scales"],dtype=np.float64),-4,4
    )
    weights=[
        np.asarray(trained["gate_weights"],dtype=np.float64),
        np.asarray(trained["gate_bias"],dtype=np.float64),
        np.asarray(trained["expert_weights"],dtype=np.float64),
        np.asarray(trained["expert_bias"],dtype=np.float64),
    ]
    gate,_,out=_fusion_forward(np,z,weights)
    target=out[0]*np.asarray(trained["target_scales"])+np.asarray(
        trained["target_means"]
    )
    risk=float(min(SIGMA_CEILING,max(SIGMA_FLOOR,exp(
        max(-20.,min(5.,float(target[0])))
    ))))
    downside=float(min(risk,max(DOWNSIDE_MIN_ANNUAL,exp(
        max(-20.,min(5.,float(target[1])))
    ))))
    probabilities=[float(x) for x in gate[0]]
    if (not isfinite(risk) or not isfinite(downside)
            or abs(sum(probabilities)-1.)>1e-8):
        raise ValueError("Nonfinite FusionNet risk forecast")
    return {
        "annualized_rms_risk_forecast":risk,
        "annualized_downside_rms_risk_forecast":downside,
        "annualized_arithmetic_return_forecast":None,
        "latent_expert_probabilities":probabilities,
        "note":"Neither downside RMS nor total RMS is a calibrated VaR/CVaR quantile.",
    }
