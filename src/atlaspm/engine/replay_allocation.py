"""Synthetic rolling allocation adapter; no fitted AI or real data approval."""
from atlaspm.engine.allocation import METHODS, optimize_target
from atlaspm.engine.contracts import Account, Holding
from atlaspm.engine.backtest import _sample_estimates


def rolling_target(candidates, history, holdings, cash, unsettled, config):
    method = config.get("method", "equal_weight")
    if method not in METHODS:
        raise ValueError("UNSUPPORTED_REPLAY_OPTIMIZER")
    lookback = config.get("lookback_sessions", 60)
    if type(lookback) is not int or lookback < 3:
        raise ValueError("INVALID_REPLAY_LOOKBACK")
    if method == "cvar" and lookback < 20:
        raise ValueError("CVAR_REQUIRES_20_PAST_RETURNS")
    if len(history) < lookback+1:
        return None
    window = history[-lookback-1:]
    symbols = [c.symbol for c in candidates]
    if not set(holdings) <= set(symbols):
        raise ValueError("OPTIMIZER_CANNOT_DISCARD_EXISTING_HOLDINGS")
    if any(not set(symbols) <= set(prices) for _, prices in window):
        raise ValueError("ROLLING_HISTORY_COVERAGE_INCOMPLETE")
    returns = [[right[1][s]/left[1][s]-1 for s in symbols]
               for left,right in zip(window,window[1:])]
    mu,cov = _sample_estimates(returns,symbols)
    # Small explicit diagonal regularization for zero variance/collinearity.
    for i in range(len(symbols)):
        cov[i][i] += 1e-8
    prices = window[-1][1]
    sectors = {c.symbol:c.sector for c in candidates}
    account = Account("SYN:REPLAY","cash",cash+unsettled,0.,0.,
                      tuple(Holding(s,q,prices[s],sectors[s]) for s,q in holdings.items()))
    constraints = dict(config)
    if constraints.get("max_gross_exposure",1) > 1:
        raise ValueError("REPLAY_CASH_ONLY")
    result = optimize_target(candidates,account,dict(symbols=symbols,covariance=cov,
                             expected_returns=mu,scenario_returns=returns),constraints)
    return dict(weights=result["weights"], method=method,
                estimation_first_date=window[0][0], estimation_last_date=window[-1][0],
                return_observations=len(returns), modeled_variance=result["modeled_variance"])
