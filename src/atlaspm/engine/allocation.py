"""Seven research optimizers behind one hard-constraint interface.

Reuse AtlasPM's existing covariance validator, inverse-volatility baseline,
and mean-variance estimator. SciPy supplies constrained optimization only.
No failed solve is silently replaced with equal weight.
"""
from __future__ import annotations

from collections import defaultdict
from math import sqrt
from typing import Any, Mapping, Sequence

from atlaspm.engine.mean_variance import _validate_covariance, optimize_mean_variance
from atlaspm.engine.optimizer import inverse_volatility_weights
from atlaspm.engine.contracts import Account, Candidate, finite

METHODS = frozenset({
    "equal_weight", "risk_parity", "minimum_variance", "mean_variance",
    "cvar", "maximum_diversification", "risk_budgeting",
})


def optimize_target(candidates: Sequence[Candidate], account: Account,
                    market: Mapping[str, Any], constraints: Mapping[str, Any], *,
                    paper: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        import numpy as np
        from scipy.optimize import linprog, minimize
    except ImportError as exc:
        raise ValueError("PORTFOLIO_OPTIMIZER_REQUIRES_PIP_INSTALL_[portfolio]") from exc
    method = constraints.get("method", "equal_weight")
    if method not in METHODS:
        raise ValueError(f"UNSUPPORTED_OPTIMIZER:{method}")
    symbols = [c.symbol for c in candidates]
    if market.get("symbols") != symbols:
        raise ValueError("COVARIANCE_SYMBOL_ORDER_MISMATCH")
    n = len(symbols)
    cov = np.asarray(_validate_covariance(market.get("covariance"), n), dtype=float)
    # The old covariance validator checks positive semidefiniteness.
    returns_raw = market.get("expected_returns", {})
    if not isinstance(returns_raw, dict) or set(returns_raw) != set(symbols):
        raise ValueError("EXPECTED_RETURN_COVERAGE_MISMATCH")
    mu = np.array([finite(returns_raw[s], f"mu.{s}") for s in symbols], dtype=float)
    cap = finite(constraints.get("max_name_weight", 0.6), "max_name_weight", minimum=0)
    cash_reserve = finite(constraints.get("min_cash_fraction", 0.1), "min_cash_fraction", minimum=0)
    exposure = finite(constraints.get("max_gross_exposure", 1.0), "max_gross_exposure", minimum=0)
    turnover_limit = finite(constraints.get("max_turnover", 2.0), "max_turnover", minimum=0)
    if cap > 1 or cash_reserve >= 1 or exposure > 4:
        raise ValueError("INVALID_PORTFOLIO_CONSTRAINT")
    # Preserve fixed-budget legacy behavior unless the caller explicitly
    # chooses cash-flexible synthetic mean-variance research.
    flexible = constraints.get("allow_cash_slack", False)
    if type(flexible) is not bool:
        raise ValueError("INVALID_CASH_SLACK_POLICY")
    if flexible and (method!="mean_variance" or account.kind!="cash"
                     or not account.account_id.startswith("SYN-")):
        raise ValueError("CASH_SLACK_REQUIRES_SYNTHETIC_CASH_MEAN_VARIANCE")
    lower_raw=market.get("expected_return_lower_bounds")
    robust_mu=mu
    if "expected_return_lower_bounds" in market:
        if (not flexible or not isinstance(lower_raw,dict)
                or set(lower_raw)!=set(symbols)):
            raise ValueError("LOWER_RETURN_BOUNDS_REQUIRE_CASH_SLACK_AND_EXACT_COVERAGE")
        robust_mu=np.array([finite(lower_raw[s],f"lower_mu.{s}") for s in symbols])
        if np.any(robust_mu>mu):
            raise ValueError("LOWER_RETURN_BOUND_EXCEEDS_ESTIMATE")
    # V1 cannot validate broker margin; risky investment remains cash-funded.
    target_total = min(exposure, 1 - cash_reserve)
    if not n or (not flexible and (target_total <= 0 or cap * n + 1e-9 < target_total)):
        raise ValueError("INFEASIBLE_NAME_CAP_OR_CASH_TARGET")
    equity = account.net_liquidation
    held = {h.symbol: h for h in account.holdings}
    if held.keys() - set(symbols):
        raise ValueError("UNMODELED_EXISTING_HOLDING_CANNOT_BE_DISCARDED")
    current = np.array([held[s].shares * held[s].price / equity if s in held else 0.0
                        for s in symbols], dtype=float)
    industries = defaultdict(list)
    for i, candidate in enumerate(candidates):
        industries[candidate.sector].append(i)
    raw_limits = constraints.get("sector_limits", {})
    if not isinstance(raw_limits, dict):
        raise ValueError("sector_limits must be a mapping")
    if set(raw_limits) - set(industries):
        raise ValueError("UNKNOWN_SECTOR_LIMIT")
    sector_limits = {s: finite(raw_limits.get(s, 1.0), f"sector.{s}", minimum=0)
                     for s in industries}
    if any(v > 1 for v in sector_limits.values()):
        raise ValueError("SECTOR_LIMIT_EXCEEDS_ONE")
    if not flexible and sum(min(sector_limits[s], cap * len(ids)) for s, ids in industries.items()) + 1e-9 < target_total:
        raise ValueError("INFEASIBLE_SECTOR_CAPS")

    target_budget = float(target_total)
    bounds = [(0., cap)] * n
    base = ([{"type":"ineq","fun":lambda x:float(target_budget-np.sum(x))}]
            if flexible else [{"type":"eq","fun":lambda x:float(np.sum(x)-target_budget)}])
    for sector, ids in industries.items():
        limit = sector_limits[sector]
        base.append({"type": "ineq", "fun": lambda x, idx=tuple(ids), lim=limit:
                    float(lim - np.sum(x[list(idx)]))})
    # Absolute-value turnover is intentionally conservative: sum |new-old|
    # excludes the cash leg and is checked again after integer rounding.
    base.append({"type": "ineq", "fun": lambda x:
                float(turnover_limit - np.sum(np.abs(x - current)))})
    budget = np.array([1.0 / n] * n)
    sigma = np.sqrt(np.diag(cov))
    if method == "risk_parity":
        seed = inverse_volatility_weights(dict(zip(symbols, map(float, sigma))), max_weight=max(cap / target_total, 1/n))
        budget = np.array([seed[s] for s in symbols], dtype=float)
    elif method == "mean_variance" and not flexible:
        # Keep prior tested AtlasPM mean-variance implementation as initializer.
        seed = optimize_mean_variance(dict(zip(symbols, map(float, mu))),
                                     cov.tolist(), max_weight=max(cap / target_total, 1/n),
                                     risk_aversion=finite(constraints.get("risk_aversion", 4.0), "risk_aversion", minimum=0.00001))
        budget = np.array([seed["weights"][s] for s in symbols], dtype=float)
    x0 = budget * target_total
    vol_cap=shock=stress_cap=None
    buy_budget=buy_multiplier=fee_reserve=None
    if flexible:
        # Reserve worst-case per-leg fees and adverse execution prices before
        # the continuous solve. Unsettled sale proceeds never increase this
        # budget. The exact integer planner still independently checks cash.
        assumptions={} if paper is None else paper
        if not isinstance(assumptions,Mapping):
            raise ValueError("INVALID_PAPER_COST_ASSUMPTIONS")
        fee=finite(assumptions.get("fee_per_order",1.),"fee_per_order",minimum=0)
        slip=finite(assumptions.get("slippage_bps",0.),"slippage_bps",minimum=0)
        impact=finite(assumptions.get("market_impact_bps",0.),"market_impact_bps",minimum=0)
        if slip+impact>20:
            raise ValueError("UNSUPPORTED_SLIPPAGE_OR_FILL_FRACTION")
        fee_reserve=n*fee/equity
        buy_multiplier=1+(slip+impact)/10000
        buy_budget=max(0.,account.cash/equity-cash_reserve-fee_reserve)
        # A buy-only constraint: sales cannot offset positive deltas.
        base.append({"type":"ineq","fun":lambda x:float(
            buy_budget-buy_multiplier*np.maximum(x-current,0.).sum())})
        vol_cap=finite(constraints.get("max_annual_volatility",.7),"max_annual_volatility",minimum=0)
        shock=finite(constraints.get("uniform_equity_shock",.2),"uniform_equity_shock",minimum=0)
        stress_cap=finite(constraints.get("max_stress_loss_fraction",.5),"max_stress_loss_fraction",minimum=0)
        if shock>1 or stress_cap>1:
            raise ValueError("INVALID_RISK_SHOCK_OR_CAP")
        if shock:
            target_budget=min(target_budget,stress_cap/shock)
        # Start from clipped holdings; a failed solve is never converted to
        # cash or another strategy. Independent post-solve risk checks remain.
        x0=np.minimum(np.maximum(current,0.),cap)
        for sector,ids in industries.items():
            total=float(np.sum(x0[ids]))
            if total>sector_limits[sector] and total>0:
                x0[ids]*=sector_limits[sector]/total
        total=float(np.sum(x0))
        if total>target_budget and total>0:
            x0*=target_budget/total
        v=float(x0@cov@x0)
        if v>vol_cap**2 and v>0:
            x0*=vol_cap/sqrt(v)
        base.append({"type":"ineq","fun":lambda x:float(vol_cap**2-x@cov@x)})
    budgets = constraints.get("risk_budgets", {})
    if method == "risk_budgeting":
        if not isinstance(budgets, dict) or set(budgets) != set(symbols):
            raise ValueError("RISK_BUDGET_COVERAGE_MISMATCH")
        b = np.array([finite(budgets[s], f"budget.{s}", minimum=0.000001) for s in symbols])
        b /= b.sum()
    else:
        b = np.ones(n) / n

    def variance(x: Any) -> float:
        return float(x @ cov @ x)

    def risk_loss(x: Any) -> float:
        contributions = x * (cov @ x)
        v = max(float(contributions.sum()), 1e-14)
        return float(np.sum((contributions / v - b) ** 2))

    if method == "cvar":
        scenarios = market.get("scenario_returns")
        if not isinstance(scenarios, list) or len(scenarios) < 20:
            raise ValueError("CVAR_REQUIRES_20_ALIGNED_PAST_SCENARIOS")
        matrix = np.asarray(scenarios, dtype=float)
        if matrix.shape != (len(scenarios), n) or not np.all(np.isfinite(matrix)):
            raise ValueError("INVALID_CVAR_SCENARIOS")
        confidence = finite(constraints.get("cvar_confidence", .95), "cvar_confidence")
        if not .5 < confidence < 1:
            raise ValueError("INVALID_CVAR_CONFIDENCE")
        m = len(matrix)
        # Variables w[n], VaR threshold, positive slacks[m], turnover slacks[n].
        dimension = 2*n + m + 1
        cost = np.zeros(dimension)
        cost[n] = 1.0
        cost[n+1:n+1+m] = 1. / ((1-confidence)*m)
        # Sparse block constraints avoid allocating O(m*m) zero coefficients.
        from scipy.sparse import csr_matrix, eye, hstack, vstack
        scenario_block = hstack([
            csr_matrix(-matrix), csr_matrix(-np.ones((m, 1))),
            -eye(m, format="csr"), csr_matrix((m, n))
        ], format="csr")
        ident = eye(n, format="csr")
        empty = csr_matrix((n, m+1))
        plus = hstack([ident, empty, -ident], format="csr")
        minus = hstack([-ident, empty, -ident], format="csr")
        turnover_row = hstack([csr_matrix((1, n+m+1)),
                               csr_matrix(np.ones((1, n)))], format="csr")
        sector_row = []
        sector_col = []
        for row_i, ids in enumerate(industries.values()):
            sector_row.extend([row_i]*len(ids))
            sector_col.extend(ids)
        sector_matrix = csr_matrix((np.ones(len(sector_col)), (sector_row, sector_col)),
                                   shape=(len(industries), n))
        sector_block = hstack([sector_matrix, csr_matrix((len(industries), m+n+1))],
                              format="csr")
        inequalities = vstack([scenario_block, plus, minus, turnover_row, sector_block],
                              format="csr")
        rhs = np.concatenate([np.zeros(m), current, -current, [turnover_limit],
                              [sector_limits[s] for s in industries]])
        eq = hstack([csr_matrix(np.ones((1, n))),
                     csr_matrix((1, dimension-n))], format="csr")
        lp_bounds = [(0., cap)]*n + [(None, None)] + [(0., None)]*(m+n)
        result = linprog(cost, A_ub=inequalities, b_ub=rhs,
                         A_eq=eq, b_eq=np.array([target_total]),
                         bounds=lp_bounds, method="highs")
        if not result.success:
            raise ValueError(f"OPTIMIZER_REFUSED:{method}:{result.message}")
        weights = result.x[:n]
        iterations = int(getattr(result, "nit", 0))
    else:
        if method == "equal_weight":
            objective = lambda x: float(np.sum((x - target_total / n)**2))
        elif method == "minimum_variance":
            objective = variance
        elif method == "mean_variance":
            gamma = finite(constraints.get("risk_aversion", 4.0), "risk_aversion", minimum=0.00001)
            # Annualize a one-way notional turnover cost on the same scale as mu.
            bps = finite(constraints.get("turnover_cost_bps", 0.), "turnover_cost_bps", minimum=0)
            interval = finite(constraints.get("rebalance_sessions", 1.), "rebalance_sessions", minimum=1)
            if bps >= 10000 or interval > 252:
                raise ValueError("INVALID_TURNOVER_COST_MODEL")
            penalty = bps / 10000 * 252 / interval
            objective = lambda x: .5*gamma*variance(x) - float(robust_mu @ x) + penalty*float(np.sum(np.abs(x-current)))
        elif method == "maximum_diversification":
            objective = lambda x: -float(sigma @ x) / sqrt(max(variance(x), 1e-14))
        else:
            objective = risk_loss
        result = minimize(objective, x0=x0, method="SLSQP", bounds=bounds,
                          constraints=base, options={"maxiter": 4000, "ftol": 1e-11})
        if not result.success:
            raise ValueError(f"OPTIMIZER_REFUSED:{method}:{result.message}")
        weights = result.x
        iterations = int(result.nit)

    tol = 1e-6
    actual_total=float(np.sum(weights))
    exposure_invalid=(actual_total>target_budget+tol if flexible
                      else abs(actual_total-target_total)>tol)
    if (not np.isfinite(weights).all() or exposure_invalid
        or (flexible and (variance(weights)>vol_cap**2+1e-9
                          or buy_multiplier*float(np.maximum(weights-current,0.).sum())>buy_budget+tol))
        or any(w < -tol or w > cap+tol for w in weights)
        or any(sum(weights[ids]) > sector_limits[s]+tol for s, ids in industries.items())
        or sum(abs(weights-current)) > turnover_limit+tol):
        raise ValueError("OPTIMIZER_CONSTRAINT_RESIDUAL_FAILED")
    modeled_variance = variance(weights)
    return {
        "method": method, "symbols": symbols,
        "weights": {s: float(w) for s, w in zip(symbols, weights)},
        "target_cash_fraction": 1-actual_total if flexible else 1-target_total, "iterations": iterations,
        **({"cash_slack_enabled":True,"risky_exposure_limit":target_budget,
            "modeled_risky_exposure":actual_total,
            "modeled_expected_annual_return":float(mu@weights),
            "modeled_lower_annual_return":float(robust_mu@weights) if lower_raw is not None else None,
            "lower_return_objective_used":lower_raw is not None,
            "lower_bounds_independently_calibrated":False,
            "settled_buy_budget_fraction":buy_budget,
            "modeled_buy_cash_fraction":buy_multiplier*float(np.maximum(weights-current,0.).sum()),
            "reserved_maximum_leg_fee_fraction":fee_reserve,
            "sale_proceeds_used_for_buys":False,
            "risk_constraints_in_solver":True} if flexible else {}),
        "modeled_variance": modeled_variance,
        "modeled_annual_volatility": sqrt(max(0.,modeled_variance)),
        "turnover_fraction_conservative": float(sum(abs(weights-current))),
        "research_only": True, "live_execution_enabled": False,
        "broker_order_submission_enabled": False,
    }


def _factor_stress_rows(instruments, market, limits, policy_cap):
    """Bounded local scenario approximation; not a full repricing model.

    Factor shocks are simultaneous, applied to the exact instruments in
    the optimizer. Positive scenario marks are diagnostic only: the
    constrained loss budget never credits hypothetical hedge gains.
    """
    scenarios=market.get("factor_stress_scenarios")
    if "factor_stress_scenarios" not in market and "max_factor_stress_loss_fraction" not in limits:
        return [],None
    if (not isinstance(scenarios,list) or not 1<=len(scenarios)<=20
            or "max_factor_stress_loss_fraction" not in limits):
        raise ValueError("MULTIASSET_FACTOR_STRESS_POLICY_REQUIRED")
    cap=finite(limits["max_factor_stress_loss_fraction"],"factor_stress_cap",minimum=0)
    if cap>policy_cap:
        raise ValueError("MULTIASSET_FACTOR_STRESS_CAP_EXCEEDS_POLICY")
    equity={p["identifier"]:p for p,_ in instruments if p["type"]=="equity"}
    inputs=market["inputs"]
    names=set()
    rows=[]
    for s in scenarios:
        if (not isinstance(s,dict) or set(s)!={"name","equity_returns","yield_shift_bps",
                    "volatility_shift_points","annual_financing_rate_additive"}):
            raise ValueError("MULTIASSET_INVALID_FACTOR_STRESS_SCENARIO")
        name=s["name"]
        if not isinstance(name,str) or not name or len(name)>80 or name in names:
            raise ValueError("MULTIASSET_FACTOR_STRESS_NAME")
        names.add(name)
        shocks=s["equity_returns"]
        if not isinstance(shocks,dict) or set(shocks)!=set(equity):
            raise ValueError("MULTIASSET_FACTOR_STRESS_EQUITY_COVERAGE")
        shocks={k:finite(v,"equity_shock") for k,v in shocks.items()}
        yield_bps=finite(s["yield_shift_bps"],"yield_shift_bps")
        vol_points=finite(s["volatility_shift_points"],"volatility_shift_points")
        apr_add=finite(s["annual_financing_rate_additive"],"annual_financing_rate_additive",minimum=0)
        apr=finite(market["annual_financing_rate"],"annual_financing_rate",minimum=0)+apr_add
        # Limit the explicitly approximate local shock domain. Large stress
        # events still use the existing independent full-premium-loss gate.
        if (any(abs(v)>.25 for v in shocks.values()) or abs(yield_bps)>200
                or abs(vol_points)>20 or apr>1):
            raise ValueError("MULTIASSET_FACTOR_STRESS_SHOCK_RANGE")
        dy=yield_bps/10000
        returns=[]
        for p,_ in instruments:
            i=p["identifier"]
            d=inputs[i]
            if p["type"]=="equity":
                ret=shocks[i]
            elif p["type"]=="treasury":
                convexity=finite(d.get("convexity"),"treasury_convexity",minimum=0)
                if convexity>10000:
                    raise ValueError("MULTIASSET_FACTOR_STRESS_CONVEXITY_RANGE")
                ret=-p["modified_duration"]*dy+.5*convexity*dy**2
            else:
                ds=equity[p["underlying"]]["mark_price"]*shocks[p["underlying"]]
                delta=finite(d["delta"],"delta")
                gamma=finite(d["gamma"],"gamma",minimum=0)
                vega=finite(d["vega_per_vol_point"],"vega",minimum=0)
                ret=(delta*ds+.5*gamma*ds**2+vega*vol_points)/p["mark_premium"]
            # All supported instruments are long: model marks cannot lose
            # more than their market value. A positive approximate return
            # never reduces another sleeve's loss budget.
            ret=finite(ret,"factor_stress_return")
            returns.append(max(-1.,ret))
        rows.append({"name":name,"returns":returns,
                     "financing_fraction":apr*market["horizon_days"]/365})
    return rows,cap


def optimize_multiasset(ledger, context, market, limits):
    """Joint constrained research solve over supported exact-leg instruments.

    Market inputs are same-horizon marked-instrument returns/covariance,
    not underlying returns for options. Only the existing institutional
    planner's supported actions are emitted. No invented broker preview.
    Existing option holdings are fixed; Treasury sales and new shorts are
    unsupported. Cash sale proceeds remain unsettled and cannot fund buys.
    """
    import numpy as np
    from scipy.optimize import minimize
    from datetime import timedelta
    from copy import deepcopy
    from math import floor
    from atlaspm.engine.contracts import timestamp
    from atlaspm.execution.institutional_ledger import build_institutional_ledger, _position
    from atlaspm.execution.institutional_batch import propose_multi_asset_batch
    from atlaspm.execution.institutional_gate import _policy_values
    from atlaspm.research.training_registry import digest
    if not ledger.get("_synthetic_fixture") or not str(context["account_id"]).startswith("DEMO-"):
        raise ValueError("MULTIASSET_RESEARCH_INPUT_NOT_CERTIFIED")
    account_id=context["account_id"]
    accounts=build_institutional_ledger(ledger)["accounts"]
    account=next((a for a in accounts if a["account_id"]==account_id),None)
    if account is None or not account["account_ledger_ready_for_risk_review"]:
        raise ValueError("MULTIASSET_LEDGER_NOT_READY")
    policy=context["policy"]
    _policy_values(policy)
    stamp=timestamp(ledger["valuation_at"],"valuation_at")
    if timestamp(market["as_of"],"as_of")!=stamp:
        raise ValueError("MULTIASSET_MARKET_CUTOFF_MISMATCH")
    horizon=market["horizon_days"]
    if type(horizon) is not int or not 1<=horizon<=365:
        raise ValueError("MULTIASSET_HORIZON")
    through=(stamp+timedelta(days=horizon)).date().isoformat()
    nlv=account["net_liquidation_calculated"]
    cash=account["settled_cash"]
    debt=account["margin_debt"]
    payable=account["unsettled_payable"]
    positions=account["positions"]
    new_puts=market.get("new_puts",[])
    if not isinstance(new_puts,list) or not 1<=len(positions)+len(new_puts)<=16:
        raise ValueError("MULTIASSET_UNIVERSE_LIMIT")
    if any(not isinstance(p,dict) or p.get("type")!="option" or p.get("right")!="put" for p in new_puts):
        raise ValueError("MULTIASSET_NEW_PUT_SCOPE")
    instruments=[(p,False) for p in positions]+[(_position(p,account_id),True) for p in new_puts]
    ids=[p["identifier"] for p,_ in instruments]
    if len(set(ids))!=len(ids) or market["symbols"]!=ids or set(market["inputs"])!=set(ids):
        raise ValueError("MULTIASSET_IDENTITY_OR_COVARIANCE_ORDER")
    cov=np.asarray(_validate_covariance(market["covariance"],len(ids)))
    cap=finite(policy["max_single_order_fraction"],"single_order",minimum=0)
    max_gross=finite(policy["max_gross_leverage"],"gross",minimum=0)
    max_stress=finite(policy["max_stress_loss_fraction"],"stress",minimum=0)
    reserve=finite(limits["min_cash_fraction"],"cash_reserve",minimum=0)
    gamma=finite(limits["risk_aversion"],"risk_aversion",minimum=.000001)
    cash_return=finite(market["cash_return"],"cash_return",minimum=0)
    funding=finite(market["annual_financing_rate"],"funding",minimum=0)*horizon/365
    max_turnover=finite(limits["max_turnover"],"turnover",minimum=0)
    max_vol=finite(limits["max_volatility"],"volatility",minimum=0)
    if reserve>=1 or cash_return>1 or funding>1 or cap>1 or max_gross>4 or max_stress>1:
        raise ValueError("MULTIASSET_POLICY_RANGE")
    rows=[]
    for p,new in instruments:
        i=p["identifier"]; kind=p["type"]; data=market["inputs"][i]
        observed=timestamp(data["observed_at"],"risk_observed_at")
        if p["mark_source"]!="synthetic" or not 0<=(stamp-observed).total_seconds()<=policy["max_data_age_seconds"] or p["mark_age_seconds"]>policy["max_data_age_seconds"]:
            raise ValueError("MULTIASSET_STALE_OR_UNVERIFIED_MARK")
        mu=finite(data["expected_return"],"expected_return")
        cost=finite(data["cost_bps"],"cost_bps",minimum=0)/10000
        liquidity=finite(data["max_trade_notional"],"liquidity",minimum=0)
        if abs(mu)>1 or cost>=1:
            raise ValueError("MULTIASSET_RETURN_OR_COST_RANGE")
        risk=[0.,0.,0.,0.,0.]
        if kind=="equity":
            if new or p["shares"]!=int(p["shares"]):
                raise ValueError("MULTIASSET_UNSUPPORTED_EQUITY")
            lot=1; unit=p["mark_price"]; qty=p["shares"]
            shock=context["stress_assumptions"]["equity_decline_fraction"]
        elif kind=="treasury":
            if new or p["maturity_date"]<=through or p["dv01_usd_per_bp"] is None or data["credit_class"]!="US_TREASURY":
                raise ValueError("MULTIASSET_BOND_RISK_UNSUPPORTED")
            lot=data["face_increment"]
            if type(lot) is not int or lot<=0 or p["face_value"]%lot:
                raise ValueError("MULTIASSET_BOND_LOT")
            unit=(p["clean_price_per_100"]+p["accrued_interest_per_100"])/100
            qty=p["face_value"]
            risk[0]=p["modified_duration"]*.0001
            shock=context["stress_assumptions"]["treasury_decline_fraction"]
        else:
            if p["contracts"]<=0 or p["expiration_date"]<=through or (new and p["right"]!="put"):
                raise ValueError("MULTIASSET_OPTION_SCOPE")
            if p["underlying"] not in ids or p["mark_premium"]<=0:
                raise ValueError("MULTIASSET_OPTION_UNDERLYING_OR_MARK")
            underlying=next(q for q,_ in instruments if q["identifier"]==p["underlying"])
            if underlying["type"]!="equity":
                raise ValueError("MULTIASSET_OPTION_UNDERLYING")
            delta=finite(data["delta"],"delta")
            if abs(delta)>1 or (p["right"]=="put" and delta>0) or (p["right"]=="call" and delta<0):
                raise ValueError("MULTIASSET_DELTA")
            spot=underlying["mark_price"]
            if new and data.get("trading_permission") is not True:
                raise ValueError("MULTIASSET_OPTION_PERMISSION_REQUIRED")
            risk[1]=abs(delta)*spot/p["mark_premium"]
            risk[2]=.5*finite(data["gamma"],"gamma",minimum=0)*(spot*.01)**2/p["mark_premium"]
            risk[3]=finite(data["vega_per_vol_point"],"vega",minimum=0)/p["mark_premium"]
            risk[4]=1.
            lot=1; unit=p["mark_premium"]*p["multiplier"]; qty=0 if new else p["contracts"]
            shock=1. # never claim option protection offsets independent stress
        if unit<=0 or not 0<=shock<=1:
            raise ValueError("MULTIASSET_UNIT_OR_STRESS")
        bound=min(cap*nlv,liquidity)/nlv
        if kind=="option":
            bound=min(bound,policy["max_option_premium_fraction"]/(1+cost)) if new else 0.
        initial=qty*unit/nlv
        low=max(0.,initial-bound) if kind=="equity" else initial
        high=initial+bound
        rows.append((p,new,lot,unit,qty,mu,cost,shock,risk,low,high))
    current=np.array([q*u/nlv for _,_,_,u,q,*_ in rows])
    mu=np.array([r[5] for r in rows]); costs=np.array([r[6] for r in rows])
    shocks=np.array([r[7] for r in rows])
    risk_matrix=np.array([r[8] for r in rows]).T
    risk_caps=np.array([finite(limits[k],"risk_limit",minimum=0) for k in
                        ("max_dv01_per_nlv","max_abs_delta_per_nlv","max_gamma_per_nlv","max_vega_per_nlv","max_option_weight")])
    n=len(ids)
    factor_rows,factor_cap=_factor_stress_rows(instruments,market,limits,max_stress)
    factor_returns=np.asarray([s["returns"] for s in factor_rows],dtype=float)
    factor_losses=np.maximum(-factor_returns,0.)
    factor_funding=np.asarray([s["financing_fraction"] for s in factor_rows],dtype=float)
    def factor_stress(x):
        return factor_losses@x[:n]+factor_funding*max(0.,debt/nlv-x[-1])
    def fee(x):
        return float(costs@np.abs(x[:n]-current))
    def available(x):
        return cash/nlv-float(np.maximum(x[:n]-current,0)@ (1+costs))-x[-1]
    def variance(x):
        return float(x[:n]@cov@x[:n])
    def objective(x):
        # Sales earn no cash return until settlement.
        return .5*gamma*variance(x)-float(mu@x[:n])-cash_return*available(x)+funding*(debt/nlv-x[-1])+fee(x)
    def constraints(x):
        q=x[:n]; net=1-fee(x)
        values=[net-1e-8,available(x)-payable/nlv-reserve,
                max_gross*net-float(q.sum()),max_stress*net-float(shocks@q),
                max_turnover-float(np.abs(q-current).sum())-x[-1],
                max_vol**2*net**2-variance(x)]
        values.extend(risk_caps*net-risk_matrix@q)
        if factor_rows:
            values.extend(factor_cap*net-factor_stress(x))
        # All puts on the same underlying compete for the same share backing.
        # Do not let multiple individually covered legs reuse those shares.
        for j,indices in put_groups.items():
            values.append(q[j]/rows[j][3]-sum(q[i]/rows[i][3]*rows[i][0]["multiplier"] for i in indices))
        return np.asarray(values)
    put_groups={}
    new_underlyings={p["underlying"] for p,new,*_ in rows if new}
    for i,(p,_,*_) in enumerate(rows):
        if p["type"]=="option" and p["right"]=="put" and p["underlying"] in new_underlyings:
            put_groups.setdefault(ids.index(p["underlying"]),[]).append(i)
    bounds=[(r[9],r[10]) for r in rows]+[(0.,min(debt/nlv,cap))]
    flags={"research_only":True,"trade_authorized":False,"live_execution_enabled":False,
           "approved_for_model_training":False,"approved_for_backtest":False,
           "global_optimum_verified":False,"solver":"SLSQP"}
    def feasible(x):
        if x.shape!=(n+1,) or not np.all(np.isfinite(x)):
            return False
        if any(v<lo-1e-10 or v>hi+1e-10 for v,(lo,hi) in zip(x,bounds)):
            return False
        residual=constraints(x)
        return bool(np.all(np.isfinite(residual)) and np.min(residual)>=-1e-9 and np.isfinite(objective(x)))
    try:
        result=minimize(objective,np.r_[current,0.],method="SLSQP",bounds=bounds,
                        constraints=[{"type":"ineq","fun":constraints}],options={"maxiter":500,"ftol":1e-11})
    except (ValueError,ArithmeticError,RuntimeError):
        return {**flags,"status":"BLOCKED_CONTINUOUS_SOLVE","legs":[]}
    if not result.success or not feasible(result.x):
        return {**flags,"status":"BLOCKED_CONTINUOUS_SOLVE","legs":[]}
    x=np.r_[current,0.]; legs=[]
    for i,(p,_new,lot,unit,_qty,_,cost,_,_,_,_) in enumerate(rows):
        change=(result.x[i]-current[i])*nlv/unit
        delta=(1 if change>=0 else -1)*floor(abs(change)/lot)*lot
        if not delta:
            continue
        x[i]+=delta*unit/nlv
        leg={"leg_id":"OPT-"+str(i),"identifier":p["identifier"],"fee":abs(delta)*unit*cost}
        if p["type"]=="equity":
            leg.update(action="BUY_EQUITY" if delta>0 else "SELL_EQUITY",shares=abs(delta),limit_price=unit)
        elif p["type"]=="treasury":
            leg.update(action="BUY_TREASURY",face_value=delta,dirty_price_per_100=unit*100)
        else:
            leg.update(action="BUY_PUT",contracts=delta,multiplier=p["multiplier"],underlying=p["underlying"],
                       strike=p["strike"],expiration_date=p["expiration_date"],mark_premium=p["mark_premium"],
                       limit_premium=p["mark_premium"],quote_age_seconds=p["mark_age_seconds"],
                       mark_source=p["mark_source"],trading_permission=market["inputs"][p["identifier"]]["trading_permission"])
        legs.append(leg)
    repayment=floor(result.x[-1]*nlv*100)/100
    if repayment:
        x[-1]=repayment/nlv
        legs.append({"leg_id":"OPT-DEBT","action":"REPAY_MARGIN","amount":repayment,"fee":0})
    if not feasible(x):
        return {**flags,"status":"BLOCKED_DISCRETE_CONSTRAINTS","legs":[]}
    base={**flags,"continuous_weights":dict(zip(ids,map(float,result.x[:n]))),
          "discrete_weights":dict(zip(ids,map(float,x[:n]))),"remaining_debt":debt-repayment,
          "settled_cash_after":available(x)*nlv,"expected_horizon_volatility":max(0.,variance(x))**.5,
          "risk_exposures":dict(zip(("dv01","abs_delta","gamma","vega","option_weight"),map(float,risk_matrix@x[:n]))),
          "legs":legs}
    if factor_rows:
        net=1-fee(x)
        stressed=factor_stress(x)
        base["factor_stress_review"]={
            "method":"synthetic_delta_gamma_vega_duration_convexity_loss_only",
            "max_loss_fraction":factor_cap,
            "scenario_count":len(factor_rows),
            "positive_mark_pnl_offsets_used":False,
            "denominator":"post_estimated_trade_fee_nlv",
            "full_repricing_verified":False,
            "broker_margin_verified":False,
            "scenarios":[{"name":s["name"],
                "instrument_return_approximations":dict(zip(ids,s["returns"])),
                "signed_mark_pnl_fraction":float(factor_returns[i]@x[:n]/net),
                "financing_cost_fraction":float(factor_funding[i]*max(0.,debt/nlv-x[-1])/net),
                "loss_budget_fraction":float(stressed[i]/net),
                "within_limit":bool(stressed[i]<=factor_cap*net+1e-9)}
                for i,s in enumerate(factor_rows)]}
    if not legs:
        return {**base,"status":"NO_CHANGE"}
    plan=deepcopy(context)
    plan.update(legs=legs,batch_id="OPT-"+digest([ledger,context,market,limits,legs])[:32],
                allow_margin_borrowing=False)
    plan.pop("basket_preview",None)
    # Independent planner checks exact units, marked accounting and policy.
    # Its missing-preview block is intentional; no approval is fabricated.
    plan["risk_observation"]={**plan["risk_observation"],
        "pro_forma_gross_leverage":float(x[:n].sum()/(1-fee(x))),
        "pro_forma_stress_loss_fraction":float(shocks@x[:n]/(1-fee(x)))}
    try:
        gate=propose_multi_asset_batch(ledger,plan)
    except (ValueError,TypeError,KeyError,ArithmeticError):
        return {**flags,"status":"BLOCKED_INDEPENDENT_PREFLIGHT","legs":[]}
    return {**base,"status":"BLOCKED_PENDING_INDEPENDENT_PREFLIGHT","draft_plan":plan,"independent_preflight":gate}
