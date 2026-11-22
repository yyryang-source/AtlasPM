"""AtlasPM ORIGINAL lightweight correlated-risk and downside-risk sleeve.

Conceptually learns from published dynamic covariance, shrinkage, HRP and
portfolio tail objectives WITHOUT claiming to implement DCC/HRP/Ledoit-Wolf.
Only realized returns through the signal close and two Atlas self-trained risk
heads are inputs. Portfolio gross leverage is guarded elsewhere (0-1x).
"""

from __future__ import annotations

from math import isfinite, sqrt
from typing import Any

CORRELATION_SHRINK = .35
DOWNSIDE_RISK_WEIGHT = .50
PRIOR_DISTANCE = .03
GRADIENT_STEP = .80
ITERATIONS = 80


def _project_capped_simplex(proposal: list[float], name_cap: float) -> list[float]:
    if (not proposal or isinstance(name_cap, bool) or not isfinite(name_cap)
            or name_cap <= 0 or name_cap > 1
            or len(proposal)*name_cap < 1-1e-10
            or any(not isfinite(v) for v in proposal)):
        raise ValueError("Infeasible fusion capped simplex")
    lower=min(proposal)-name_cap
    upper=max(proposal)
    for _ in range(100):
        mid=(lower+upper)*.5
        total=sum(min(name_cap,max(0.,v-mid)) for v in proposal)
        if total > 1:
            lower=mid
        else:
            upper=mid
    result=[min(name_cap,max(0.,v-upper)) for v in proposal]
    # Restore the small numerical projection error without exceeding caps.
    remaining=1-sum(result)
    for j in range(len(result)):
        if abs(remaining) < 1e-11:
            break
        adjust=max(-result[j],min(name_cap-result[j],remaining))
        result[j]+=adjust
        remaining-=adjust
    if any(v < -1e-10 or v > name_cap+1e-10 for v in result):
        raise ValueError("Fusion sleeve violates per-name cap")
    if abs(sum(result)-1)>1e-8:
        raise ValueError("Fusion sleeve weights do not total one")
    return result


def _shrunken_correlation(
    observations: list[list[float]], assets: int,
) -> list[list[float]]:
    if len(observations)<5 or any(len(row)!=assets for row in observations):
        raise ValueError("Fusion correlation requires aligned recent daily returns")
    n=len(observations)
    means=[sum(row[j] for row in observations)/n for j in range(assets)]
    centered=[
        [row[j]-means[j] for j in range(assets)] for row in observations
    ]
    if any(any(not isfinite(v) for v in row) for row in centered):
        raise ValueError("Fusion correlation contains nonfinite observations")
    var=[sum(row[j]**2 for row in centered)/n for j in range(assets)]
    correlation=[
        [0.]*assets for _ in range(assets)
    ]
    for i in range(assets):
        correlation[i][i]=1.0
        for j in range(i+1,assets):
            denom=sqrt(max(0.,var[i]*var[j]))
            c=(sum(row[i]*row[j] for row in centered)/n)/denom if denom>1e-16 else 0.
            c=max(-1.,min(1.,c))
            # Fixed 35% shrinkage of off-diagonal covariance toward zero.
            # NOT the optimal estimated Ledoit-Wolf coefficient.
            c*=1-CORRELATION_SHRINK
            correlation[i][j]=c
            correlation[j][i]=c
    return correlation


def allocate_fusion_sleeve(
    symbols: list[str],
    risk_forecasts: list[float],
    downside_forecasts: list[float],
    trailing_returns: list[list[float]],
    *,
    max_weight: float,
    mode: str = "shrinkage",
) -> dict[str, Any]:
    if mode not in ("inverse_risk","downside","shrinkage"):
        raise ValueError("Unknown Atlas Fusion allocator ablation mode")
    n=len(symbols)
    if not 2<=n<=12 or len(set(symbols))!=n:
        raise ValueError("Fusion allocation requires 2-12 distinct assets")
    if len(risk_forecasts)!=n or len(downside_forecasts)!=n:
        raise ValueError("Fusion risk output symbols do not align")
    if any(
        isinstance(v,bool) or not isinstance(v,(int,float)) or
        not isfinite(v) or v<=0 or v>8
        for v in [*risk_forecasts,*downside_forecasts]
    ):
        raise ValueError("Fusion risk/downside forecasts must be positive finite")
    from atlaspm.ml_dynamic_rebalance import _real
    max_weight=_real(max_weight,"max_weight",1/n,1)
    correlation=_shrunken_correlation(trailing_returns,n)
    combined=[max(.03,r+.5*d) for r,d in zip(risk_forecasts,downside_forecasts)]
    inverse=[1/x for x in combined]
    normalized=[x/sum(inverse) for x in inverse]
    prior=_project_capped_simplex(normalized,max_weight)
    inverse_risk=[1/max(.03,v) for v in risk_forecasts]
    simple_inverse=_project_capped_simplex(
        [v/sum(inverse_risk) for v in inverse_risk],max_weight
    )
    # Squared-risk objective balances estimated simultaneous losses with an
    # explicit tail-risk squared penalty, plus shrink to stable prior.
    covariance=[
        [
            correlation[i][j]*(risk_forecasts[i]/.18)*(risk_forecasts[j]/.18)
            + (
                (DOWNSIDE_RISK_WEIGHT*downside_forecasts[i]/.18)**2
                if i==j else 0.
            )
            for j in range(n)
        ] for i in range(n)
    ]
    scale=max(1.,max(sum(abs(v) for v in row) for row in covariance))
    matrix=[[x/scale for x in row] for row in covariance]
    current=(
        simple_inverse[:] if mode == "inverse_risk" else prior[:]
    )
    for _ in range(ITERATIONS if mode == "shrinkage" else 0):
        gradient=[
            2*sum(matrix[i][j]*current[j] for j in range(n))
            +2*PRIOR_DISTANCE*(current[i]-prior[i])
            for i in range(n)
        ]
        step=GRADIENT_STEP/max(1.,2*max(sum(abs(v) for v in row) for row in matrix))
        current=_project_capped_simplex(
            [a-step*g for a,g in zip(current,gradient)],max_weight
        )
    if any(not isfinite(w) for w in current):
        raise ValueError("Fusion optimizer generated nonfinite weights")
    score=sum(
        current[i]*matrix[i][j]*current[j]
        for i in range(n) for j in range(n)
    )
    return {
        "weights":dict(zip(symbols,current)),
        "mode":mode,
        "inverse_risk_prior":dict(zip(symbols,simple_inverse)),
        "inverse_risk_downside_prior":dict(zip(symbols,prior)),
        "sample_correlation_shrunk_offdiag":correlation,
        "correlation_shrinkage_fixed":CORRELATION_SHRINK,
        "downside_risk_weight":DOWNSIDE_RISK_WEIGHT,
        "portfolio_proxy_variance_objective":score,
        "source":"past_only_sample_shrunken_correlation_with_fusion_predictions",
        "does_not_implement":"DCC-GARCH or HRP or Ledoit-Wolf fitted shrinkage",
        "trained_on_future_returns":False,
    }
