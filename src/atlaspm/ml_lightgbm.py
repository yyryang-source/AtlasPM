"""Optional LightGBM risk forecasting, comparable to Qlib's model family.

This imports the independently published LightGBM package at fit time. It is
NOT proprietary Microsoft/IBKR code, NOT a full Qlib Alpha158 workflow, and
NOT a pretrained model. The research feature/label timeline is shared with
AtlasPM's ridge baseline; every fit uses only fully observed past labels.
"""

from __future__ import annotations

from math import isfinite, log

from atlaspm.ml_dynamic_rebalance import FEATURE_NAMES, SIGMA_CEILING, SIGMA_FLOOR, _features, _rms

LIGHTGBM_PARAMS = {
    "objective": "regression",
    "n_estimators": 65,
    "learning_rate": 0.05,
    "max_depth": 3,
    "num_leaves": 7,
    "min_child_samples": 5,
    "reg_lambda": 2.0,
    "subsample": 1.0,
    "colsample_bytree": 1.0,
    "random_state": 1729,
    "n_jobs": 1,
    "verbosity": -1,
}


def _dependency():
    try:
        import lightgbm as lgb
    except (ImportError, OSError) as exc:
        raise ValueError(
            "LightGBM is optional; install with python -m pip install -e '.[ml]'"
        ) from exc
    return lgb


def train_lightgbm_risk(
    asset_returns: list[float], signal_index: int, lookback: int,
    horizon: int, minimum_samples: int,
) -> dict:
    """At signal t, train only labels ending <=t, and preserve a native model dump."""
    lgb = _dependency()
    last_sample = signal_index - horizon
    points = range(lookback, last_sample + 1)
    if last_sample < lookback or len(points) < minimum_samples:
        raise ValueError("Insufficient fully observed LightGBM training labels")
    x = [_features(asset_returns, i, lookback) for i in points]
    y = [
        log(max(SIGMA_FLOOR, _rms(asset_returns[i:i+horizon])))
        for i in points
    ]
    model = lgb.LGBMRegressor(**LIGHTGBM_PARAMS)
    model.fit(x, y, feature_name=list(FEATURE_NAMES))
    raw = model.booster_.model_to_string()
    if not isinstance(raw, str) or not raw:
        raise ValueError("LightGBM produced no trained model artifact")
    return {
        "model": "trained_lightgbm_log_forward_rms_risk",
        "training_samples": len(points),
        "training_first_signal_index": lookback,
        "training_last_signal_index": last_sample,
        "label_fully_observed_through_price_index": last_sample + horizon,
        "target": "log_next_horizon_annualized_root_mean_square_daily_return",
        "feature_names": list(FEATURE_NAMES),
        "hyperparameters": dict(LIGHTGBM_PARAMS),
        "feature_importance_split": [
            int(v) for v in model.booster_.feature_importance(importance_type="split")
        ],
        "trained_native_model_text_private": raw,
        "source_project": "lightgbm-org/LightGBM",
        "research_pattern_reference": "microsoft/qlib LightGBM benchmarks",
        "is_official_qlib_pipeline": False,
        "is_pretrained_institutional_model": False,
        "model_validated_for_live_trading": False,
    }


def predict_lightgbm_risk(model: dict, feature_row: list[float]) -> float:
    lgb = _dependency()
    if model.get("model") != "trained_lightgbm_log_forward_rms_risk":
        raise ValueError("Unsupported LightGBM research model")
    if len(feature_row) != len(FEATURE_NAMES) or any(
        not isinstance(v, (int, float)) or isinstance(v, bool) or not isfinite(v)
        for v in feature_row
    ):
        raise ValueError("Non-finite LightGBM prediction features")
    booster = lgb.Booster(model_str=model["trained_native_model_text_private"])
    prediction = float(booster.predict([feature_row])[0])
    if not isfinite(prediction):
        raise ValueError("Non-finite LightGBM risk forecast")
    from math import exp
    return min(SIGMA_CEILING, max(SIGMA_FLOOR, exp(max(-20.0, min(5.0, prediction)))))
