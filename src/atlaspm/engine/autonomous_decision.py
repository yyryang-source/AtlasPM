"""Research-only ranking and synthetic portfolio bridge; no broker imports."""
from __future__ import annotations
from copy import deepcopy
from atlaspm.engine.contracts import finite, timestamp, Account
from atlaspm.research.financial_events import FLAGS
from atlaspm.research.signal_fusion import fuse
from atlaspm.research.research_contract import contract
from atlaspm.research.training_registry import digest
from atlaspm.engine.unified import run_unified_portfolio, SAFETY


def decide(rows, *, as_of, weights, horizon_days, cost_fraction, max_disagreement=.05):
    cost=finite(cost_fraction,"cost_fraction",minimum=0)
    if cost>=1 or type(horizon_days) is not int or not 1<=horizon_days<=730:
        raise ValueError("INVALID_DECISION_POLICY")
    fusion=fuse(rows,as_of=as_of,weights=weights,max_disagreement=max_disagreement)
    out=[]
    for g in fusion["groups"]:
        if g["horizon_days"]!=horizon_days:
            continue
        lower,upper=g["lower_bound"],g["upper_bound"]
        action="NO_TRADE"
        reasons=list(g["blocked_reasons"])
        if not reasons:
            if lower>cost:
                action="BUY"
            elif upper < -cost:
                action="SELL"
            else:
                action="HOLD"
                reasons.append("NO_ROBUST_EDGE_AFTER_COST")
        out.append({**g,"action":action,"reasons":reasons,"cost_fraction":cost,
                    "robust_net_edge":lower-cost if action=="BUY" else -upper-cost if action=="SELL" else None,
                    "invalidation":["evidence expires","risk or liquidity limit breached","opposing evidence"],
                    "trade_authorized":False})
    out.sort(key=lambda x:(-(x["robust_net_edge"] or 0),x["security_id"],x["asset_type"]))
    return {**FLAGS,"model":"autonomous_decision_v1","as_of":as_of,"candidates":out,
            "fusion":fusion,"trade_authorized":False}


def portfolio_cycle(payload, rows, *, weights, cost_fraction, liquidity, horizon_days=365):
    """All-or-nothing synthetic held-equity rebalance. HOLD/NO_TRADE never
    gets converted to a zero target, and no unsupported asset gets an order.
    The existing portfolio engine owns target weights and exact-leg approvals.
    """
    def blocked(reason, **extra):
        return {**FLAGS,"decision":"NO_TRADE","reason":reason,**extra}
    if (not isinstance(payload,dict) or payload.get("mode")!="synthetic" or
            payload.get("synthetic_fixture") is not True or
            any(k in payload and payload[k] is not v for k,v in {**FLAGS,**SAFETY}.items())):
        return blocked("SYNTHETIC_SCOPE_REQUIRED")
    try:
        account=Account.from_payload(payload["account"])
        if not account.account_id.startswith("SYN-") or account.kind!="cash":
            return blocked("SYNTHETIC_CASH_ACCOUNT_REQUIRED")
        if len(account.holdings)>20 or not isinstance(liquidity,dict):
            raise ValueError("BOUNDED_SYNTHETIC_UNIVERSE_REQUIRED")
        # Existing portfolio covariance/expected returns are annual; no
        # implicit scaling of a different forecast horizon.
        if horizon_days!=365:
            return blocked("ANNUAL_HORIZON_REQUIRED_BY_PORTFOLIO")
        as_of=payload["as_of"]
        decision=decide(rows,as_of=as_of,weights=weights,horizon_days=horizon_days,cost_fraction=cost_fraction)
        groups={g["security_id"]:g for g in decision["candidates"]}
        symbols=payload["market"]["symbols"]
        if len(symbols)!=len(groups) or set(symbols)!={h.symbol for h in account.holdings} or set(liquidity)!=set(symbols):
            return blocked("UNIVERSE_COVERAGE_MISMATCH",research=decision)
        if any(groups.get("SYN:"+s,{}).get("action")!="BUY" or groups["SYN:"+s]["asset_type"] not in {"stock","etf"} for s in symbols):
            return blocked("NO_COMPLETE_POSITIVE_HELD_EQUITY_SET",research=decision)
        adapted=deepcopy(payload)
        adapted["signals"]=[]
        ranks={g["security_id"]:i+1 for i,g in enumerate(decision["candidates"])}
        validated=[contract(row,as_of=as_of) for row in rows]
        plans=[]
        for symbol in symbols:
            g=groups["SYN:"+symbol]
            relevant=[v for v in validated if v["research_id"] in g["research_ids"] and v["numeric_eligible"]]
            if not relevant:
                raise ValueError("NO_NUMERIC_EVIDENCE")
            available=min(v["data_available_at"] for v in relevant)
            expires=min(v["contract"]["expires_at"] for v in relevant)
            hold=next(h for h in account.holdings if h.symbol==symbol)
            info=liquidity[symbol]
            if not isinstance(info,dict) or set(info)!={"daily_dollar_volume","spread_bps","observed_at","source"} or info["source"]!="synthetic":
                raise ValueError("SYNTHETIC_LIQUIDITY_REQUIRED")
            now=timestamp(as_of,"as_of")
            quote=timestamp(info["observed_at"],"liquidity_time")
            if not 0<=(now-quote).total_seconds()<=120:
                raise ValueError("STALE_LIQUIDITY")
            adv=finite(info["daily_dollar_volume"],"adv",minimum=1)
            spread=finite(info["spread_bps"],"spread",minimum=0)
            fee=finite(payload.get("paper",{}).get("fee_per_order",1),"fee",minimum=0)
            slip=finite(payload.get("paper",{}).get("slippage_bps",0),"slip",minimum=0)
            impact=finite(payload.get("paper",{}).get("market_impact_bps",0),"impact",minimum=0)
            minimum=finite(payload["constraints"].get("min_trade_notional",100),"minimum",minimum=1)
            conservative_cost=(spread+2*(slip+impact))/10000+2*fee/minimum
            if cost_fraction+1e-12<conservative_cost:
                raise ValueError("COST_BUDGET_UNDERSTATED")
            max_ticket=finite(payload["constraints"].get("max_single_order_fraction",1),"ticket",minimum=0)*account.net_liquidation
            if max_ticket>adv*.01 or spread>20:
                raise ValueError("LIQUIDITY_CAP")
            adapted["signals"].append({"symbol":symbol,"security_id":"SYN:"+symbol,"sector":hold.sector,
                "identity_source":"synthetic","data_source":"synthetic","data_quality":"synthetic_test_only",
                "research_eligible":True,"model_approved_for_training":False,
                "generated_at":as_of,"available_at":as_of,"data_available_at":available,"expires_at":expires,
                "factor_score":g["robust_net_edge"],"model_rank":ranks["SYN:"+symbol],
                "model_version":digest(g)})
            adapted["market"]["expected_returns"][symbol]=g["expected_return"]
            plans.append({"symbol":symbol,"research_ids":g["research_ids"],"entry_mark":hold.price,
                "horizon_days":horizon_days,"invalidation":g["invalidation"],"estimated_round_trip_cost_fraction":cost_fraction,
                "daily_dollar_volume":adv,"spread_bps":spread,"risk_budget":deepcopy(payload["constraints"]),
                "target_weight":None,"independent_risk_approval":False})
        portfolio=run_unified_portfolio(adapted)
        if portfolio["decision"]!="SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE":
            return blocked("PORTFOLIO_DID_NOT_COMPLETE",research=decision,portfolio=portfolio)
        for plan in plans:
            plan["target_weight"]=portfolio["allocation"]["weights"][plan["symbol"]]
            plan["synthetic_risk_gate_passed"]=True
        return {**FLAGS,"decision":"SYNTHETIC_ENTRY_REVIEW_COMPLETE","research":decision,"plans":plans,
                "portfolio":portfolio,"trade_authorized":False,"input_sha256":digest(adapted)}
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        return blocked(str(exc))


def multiasset_position_review(position, *, as_of, risk_inputs):
    """Synthetic-only asset-specific exposure diagnostics; never an order.

    Reuses the institutional ledger's position valuation rather than treating
    contract count or face value as stock shares. No modelled margin credit.
    """
    from atlaspm.execution.institutional_ledger import _position
    from datetime import date
    if not isinstance(position,dict) or position.get("mark_source")!="synthetic" or not str(position.get("identifier","")).startswith("SYN:"):
        raise ValueError("SYNTHETIC_POSITION_REQUIRED")
    if not isinstance(risk_inputs,dict):
        raise ValueError("ASSET_SPECIFIC_RISK_INPUTS_REQUIRED")
    now=timestamp(as_of,"as_of")
    observed=timestamp(risk_inputs["observed_at"],"risk_observed_at")
    if not 0<=(now-observed).total_seconds()<=120:
        raise ValueError("STALE_ASSET_RISK_INPUTS")
    p=_position(position,"SYN-REVIEW")
    if p["mark_age_seconds"]>120:
        raise ValueError("STALE_POSITION_MARK")
    blockers=["NO_INDEPENDENT_ASSET_RISK_APPROVAL"]
    metrics={"market_value":p["market_value"],"gross_marked_notional":p["gross_notional"]}
    kind=p["type"]
    if kind=="equity":
        adv=finite(risk_inputs["daily_dollar_volume"],"adv",minimum=1)
        metrics["position_to_daily_dollar_volume"]=p["market_value"]/adv
    elif kind=="option":
        if date.fromisoformat(p["expiration_date"])<=now.date():
            raise ValueError("OPTION_EXPIRED_OR_EXPIRING_TODAY")
        underlying=finite(risk_inputs["underlying_price"],"underlying_price",minimum=.000001)
        delta=finite(risk_inputs["delta"],"delta")
        gamma=finite(risk_inputs["gamma"],"gamma",minimum=0)
        vega=finite(risk_inputs["vega_per_vol_point"],"vega",minimum=0)
        if abs(delta)>1 or (p["right"]=="call" and delta<0) or (p["right"]=="put" and delta>0):
            raise ValueError("INVALID_OPTION_DELTA")
        units=p["contracts"]*p["multiplier"]
        metrics.update(delta_dollar_exposure=units*delta*underlying,
                       gamma_pnl_for_one_percent_move=units*.5*gamma*(underlying*.01)**2,
                       vega_pnl_per_vol_point=units*vega,
                       long_premium_at_risk=p["market_value"] if units>0 else None)
        if units<0:
            blockers.append("SHORT_OPTION_MARGIN_UNVERIFIED")
        blockers.append("OPTION_EXECUTION_UNSUPPORTED_BY_RESEARCH_BRIDGE")
    elif kind=="treasury":
        if date.fromisoformat(p["maturity_date"])<=now.date():
            raise ValueError("BOND_MATURED")
        if p["dv01_usd_per_bp"] is None:
            raise ValueError("BOND_DURATION_REQUIRED")
        metrics.update(dv01_usd_per_bp=p["dv01_usd_per_bp"],
                       illustrative_100bp_rate_loss=p["dv01_usd_per_bp"]*100,
                       credit_rating=risk_inputs.get("credit_rating"),
                       recognized_collateral=0.)
        blockers.extend(["CREDIT_AND_COLLATERAL_UNVERIFIED","BOND_EXECUTION_UNSUPPORTED_BY_RESEARCH_BRIDGE"])
    debt=finite(risk_inputs.get("financing_debt",0),"financing_debt",minimum=0)
    rate=finite(risk_inputs.get("annual_financing_rate",0),"financing_rate",minimum=0)
    metrics["annual_financing_cost_assumption"]=debt*rate
    if debt:
        blockers.append("BROKER_MARGIN_UNVERIFIED")
    # No automatic plan because these marks, Greeks and credit are assumed.
    return {**FLAGS,"asset_type":kind,"metrics":metrics,"decision":"NO_TRADE",
            "block_reasons":blockers,"trade_authorized":False,"broker_margin_verified":False}
