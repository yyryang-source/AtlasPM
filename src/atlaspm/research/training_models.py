"""Optional tabular estimators. No model is approved for deployment."""
from __future__ import annotations
from importlib import metadata
from math import isfinite


def json_parameters(value):
    """Normalize estimator metadata without allowing nonfinite JSON numbers."""
    if isinstance(value,dict):
        return {str(k):json_parameters(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [json_parameters(v) for v in value]
    if value is None or isinstance(value,(str,bool,int)):
        return value
    if isinstance(value,float):
        return value if isfinite(value) else ('NaN' if value!=value else ('Infinity' if value>0 else '-Infinity'))
    if hasattr(value,'item'):
        return json_parameters(value.item())
    raise ValueError(f'UNSUPPORTED_ESTIMATOR_PARAMETER:{type(value).__name__}')

MODELS = ("ridge", "historical_mean", "elastic_net", "random_forest",
          "lightgbm", "xgboost", "catboost", "mlp")
PACKAGES = {"elastic_net":"scikit-learn","random_forest":"scikit-learn",
            "mlp":"scikit-learn","lightgbm":"lightgbm","xgboost":"xgboost",
            "catboost":"catboost"}


def versions(models):
    result = {}
    for name in sorted({"numpy"} | {PACKAGES[m] for m in models if m in PACKAGES}):
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError as exc:
            raise ValueError(f"MODEL_DEPENDENCY_MISSING:{name}; install .[research-ml]") from exc
    return result


def fit_predict(name, x, y, test, seed):
    """Fit only the provided training fold. Fixed settings, no OOS tuning."""
    if name == "elastic_net":
        from sklearn.linear_model import ElasticNet
        model = ElasticNet(alpha=1e-5,l1_ratio=.5,max_iter=10000,random_state=seed)
    elif name == "random_forest":
        from sklearn.ensemble import RandomForestRegressor
        model = RandomForestRegressor(n_estimators=64,max_depth=5,min_samples_leaf=3,
                                      random_state=seed,n_jobs=1)
    elif name == "mlp":
        from sklearn.neural_network import MLPRegressor
        model = MLPRegressor(hidden_layer_sizes=(32,16),solver="lbfgs",alpha=.01,
                             max_iter=500,random_state=seed)
    elif name == "lightgbm":
        from lightgbm import LGBMRegressor
        model = LGBMRegressor(n_estimators=64,num_leaves=15,min_child_samples=5,
                              learning_rate=.05,random_state=seed,n_jobs=1,
                              verbosity=-1,deterministic=True,force_col_wise=True)
    elif name == "xgboost":
        from xgboost import XGBRegressor
        model = XGBRegressor(n_estimators=64,max_depth=3,learning_rate=.05,
                             objective="reg:squarederror",random_state=seed,n_jobs=1,
                             tree_method="hist")
    elif name == "catboost":
        from catboost import CatBoostRegressor
        model = CatBoostRegressor(iterations=64,depth=4,learning_rate=.05,
                                  random_seed=seed,thread_count=1,verbose=False,
                                  allow_writing_files=False)
    else:
        raise ValueError("UNKNOWN_OPTIONAL_MODEL")
    # Normalize the target using training labels only; especially important
    # for neural models predicting small daily returns.
    import numpy as np
    mean=float(np.mean(y))
    scale=max(float(np.std(y)),1e-12)
    model.fit(x,(y-mean)/scale)
    predicted=np.asarray(model.predict(test),dtype=float)*scale+mean
    if predicted.shape!=(len(test),) or not np.isfinite(predicted).all():
        raise ValueError(f"INVALID_MODEL_OUTPUT:{name}")
    return predicted, json_parameters(model.get_params())
