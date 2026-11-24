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


def fit_predict(name, x, y, test, seed, *, capture_weights=None):
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
    if capture_weights is not None:
        capture_weights(dict(target_mean=mean,target_scale=scale,model=export_estimator(name,model)))
    return predicted, json_parameters(model.get_params())


def export_estimator(name,model):
    """Store numeric sklearn estimators and library-native tree formats."""
    if name=="elastic_net":
        return dict(coefficients=model.coef_.tolist(),intercept=float(model.intercept_))
    if name=="mlp":
        return dict(coefficients=[v.tolist() for v in model.coefs_],
                    intercepts=[v.tolist() for v in model.intercepts_])
    if name=="random_forest":
        return dict(trees=[dict(left=t.tree_.children_left.tolist(),
                    right=t.tree_.children_right.tolist(),feature=t.tree_.feature.tolist(),
                    threshold=t.tree_.threshold.tolist(),
                    value=t.tree_.value[:,0,0].tolist()) for t in model.estimators_])
    if name=="lightgbm":
        return dict(native=model.booster_.model_to_string())
    if name=="xgboost":
        return dict(native=bytes(model.get_booster().save_raw(raw_format="json")).decode("utf-8"))
    if name=="catboost":
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"model.json"
            model.save_model(str(path),format="json")
            return dict(native=path.read_text(encoding="utf-8"))
    raise ValueError("UNKNOWN_OPTIONAL_MODEL")


def load_estimator_predictor(name,weights,feature_count):
    """Return a batch predictor; no pickle or arbitrary Python deserialization."""
    import numpy as np
    if name=="elastic_net":
        coef=np.asarray(weights["coefficients"],dtype=float)
        intercept=float(weights["intercept"])
        if coef.shape!=(feature_count,) or not np.isfinite(coef).all() or not isfinite(intercept):
            raise ValueError("ARTIFACT_LINEAR_INVALID")
        return lambda x:x@coef+intercept
    if name=="mlp":
        coefs=[np.asarray(v,dtype=float) for v in weights["coefficients"]]
        biases=[np.asarray(v,dtype=float) for v in weights["intercepts"]]
        dims=(feature_count,32,16,1)
        if len(coefs)!=3 or len(biases)!=3 or any(
            a.shape!=(dims[i],dims[i+1]) or b.shape!=(dims[i+1],)
            or not np.isfinite(a).all() or not np.isfinite(b).all()
            for i,(a,b) in enumerate(zip(coefs,biases))):
            raise ValueError("ARTIFACT_MLP_INVALID")
        def predict(x):
            for i,(a,b) in enumerate(zip(coefs,biases)):
                x=x@a+b
                if i<2:
                    x=np.maximum(x,0)
            return x[:,0]
        return predict
    if name=="random_forest":
        trees=[]
        for tree in weights["trees"]:
            left,right,feature=(np.asarray(tree[k],dtype=np.int64) for k in ("left","right","feature"))
            threshold,value=(np.asarray(tree[k],dtype=float) for k in ("threshold","value"))
            n=len(left)
            if not n or any(v.shape!=(n,) for v in (right,feature,threshold,value)) or not np.isfinite(threshold).all() or not np.isfinite(value).all():
                raise ValueError("ARTIFACT_FOREST_INVALID")
            for i in range(n):
                if left[i]==right[i]==-1:
                    continue
                if not (i<left[i]<n and i<right[i]<n and 0<=feature[i]<feature_count):
                    raise ValueError("ARTIFACT_FOREST_INVALID")
            trees.append((left,right,feature,threshold,value))
        if not trees:
            raise ValueError("ARTIFACT_FOREST_EMPTY")
        def predict(x):
            x=np.asarray(x,dtype=np.float32)
            out=np.zeros(len(x),dtype=float)
            for left,right,feature,threshold,value in trees:
                nodes=np.zeros(len(x),dtype=np.int64)
                while True:
                    active=np.flatnonzero(left[nodes]!=-1)
                    if not len(active):
                        break
                    nd=nodes[active]
                    nodes[active]=np.where(x[active,feature[nd]]<=threshold[nd],left[nd],right[nd])
                out+=value[nodes]
            return out/len(trees)
        return predict
    if name=="lightgbm":
        from lightgbm import Booster
        return Booster(model_str=weights["native"]).predict
    if name=="xgboost":
        from xgboost import Booster,DMatrix
        booster=Booster()
        booster.load_model(bytearray(weights["native"].encode("utf-8")))
        return lambda x:booster.predict(DMatrix(x))
    if name=="catboost":
        from catboost import CatBoostRegressor
        from tempfile import TemporaryDirectory
        from pathlib import Path
        model=CatBoostRegressor()
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"model.json"
            path.write_text(weights["native"],encoding="utf-8")
            model.load_model(str(path),format="json")
        return model.predict
    raise ValueError("UNKNOWN_OPTIONAL_MODEL")


class ExpandingRidge:
    """Exact expanding-window sufficient statistics, up to floating-point rounding.

    Each matured row is consumed once. Batch-combined centered moments avoid
    catastrophic cancellation from raw second-moment subtraction.
    """
    def __init__(self,features):
        import numpy as np
        self.count=0
        self.mean=np.zeros(features,dtype=np.float64)
        self.target_mean=0.
        self.scatter=np.zeros((features,features),dtype=np.float64)
        self.cross=np.zeros(features,dtype=np.float64)

    def update_prefix(self,rows,*,batch_size=4096):
        import numpy as np
        if len(rows)<self.count:
            raise ValueError("NONEXPANDING_RIDGE_PREFIX")
        initial=self.count
        for start in range(initial,len(rows),batch_size):
            stop=min(start+batch_size,len(rows))
            batch=[rows[i] for i in range(start,stop)]
            x=np.asarray([r["x"] for r in batch],dtype=np.float64)
            y=np.asarray([r["y"] for r in batch],dtype=np.float64)
            if not np.isfinite(x).all() or not np.isfinite(y).all():
                raise ValueError("INVALID_INCREMENTAL_RIDGE_INPUT")
            n=len(x)
            mean=x.mean(axis=0)
            target=float(y.mean())
            dx=mean-self.mean
            dy=target-self.target_mean
            combined=self.count+n
            factor=self.count*n/combined
            x-=mean
            y-=target
            self.scatter+=x.T@x+factor*np.outer(dx,dx)
            self.cross+=x.T@y+factor*dx*dy
            self.mean+=dx*n/combined
            self.target_mean+=dy*n/combined
            self.count=combined
        return self.count-initial

    def solve(self,alpha):
        import numpy as np
        if not self.count:
            raise ValueError("EMPTY_INCREMENTAL_RIDGE")
        scale=np.sqrt(np.maximum(np.diag(self.scatter)/self.count,0.))
        scale=np.where(scale>1e-12,scale,1.)
        gram=self.scatter/scale[:,None]/scale[None,:]
        coef=np.linalg.solve(gram+alpha*np.eye(len(scale)),self.cross/scale)
        if not np.isfinite(coef).all():
            raise ValueError("INVALID_INCREMENTAL_RIDGE_OUTPUT")
        return self.mean.copy(),scale,coef,self.target_mean
