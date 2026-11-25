"""Bounded synthetic cash replay performance, attribution and stress review.

The replay ledger is authoritative. These are modeled execution costs,
not broker TCA. No calibration, model selection or automatic promotion.
"""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from math import fsum, isclose
from atlaspm.engine.contracts import finite
from atlaspm.engine.replay import replay
from atlaspm.engine.replay_performance import metrics
from atlaspm.execution.oms_controls import verify_audit_chain
from atlaspm.research.training_registry import FLAGS, digest


def _same(a,b,label):
    if not isclose(a,b,rel_tol=1e-9,abs_tol=1e-6):
        raise ValueError("REPLAY_REVIEW_RECONCILIATION:"+label)


def attribution(payload,run):
    """Reconcile marked P&L and model order costs by symbol and sector."""
    sessions=payload["sessions"]
    curve=run["curve"]
    dates=[m["date"] for m in sessions]
    if [r["date"] for r in curve]!=dates or len(set(dates))!=len(dates):
        raise ValueError("REPLAY_REVIEW_DATE_ALIGNMENT")
    if run.get("execution_model")!="synthetic_next_open_cash_only":
        raise ValueError("REPLAY_REVIEW_CASH_ONLY")
    initial=finite(payload["initial_cash"],"initial_cash",minimum=.01)
    orders=defaultdict(list)
    trades=defaultdict(list)
    for r in run["orders"]:
        if r["date"] not in dates:
            raise ValueError("REPLAY_REVIEW_ORDER_DATE")
        orders[r["date"]].append(r)
    for r in run["trades"]:
        if r["date"] not in dates:
            raise ValueError("REPLAY_REVIEW_TRADE_DATE")
        trades[r["date"]].append(r)
    totals=defaultdict(lambda:defaultdict(float))
    daily=[]
    previous={}
    previous_close={}
    prior_equity=initial
    order_ids=set()
    for row,mark in zip(curve,sessions):
        day=row["date"]
        current=row["holdings"]
        by_symbol=defaultdict(lambda:defaultdict(float))
        expected=dict(previous)
        order_fills=defaultdict(int)
        trade_fills=defaultdict(int)
        for order in orders[day]:
            s=order["symbol"]
            if order["order_id"] in order_ids or not verify_audit_chain(order["events"],order["audit_root_sha256"]):
                raise ValueError("REPLAY_REVIEW_ORDER_AUDIT")
            order_ids.add(order["order_id"])
            q=order["filled"]
            if (order["side"] not in ("BUY","SELL") or type(q) is not int
                    or q<0 or q+order["unfilled"]!=order["requested"]):
                raise ValueError("REPLAY_REVIEW_ORDER_QUANTITY")
            mark_price=mark["open"][s]
            fee=finite(order["fee"],"fee",minimum=0)
            adverse=q*abs(order["price"]-mark_price)
            _same(order["cost"],fee+adverse,"order_cost")
            impact=q*mark_price*order["impact_bps"]/10000
            slip=q*mark_price*payload.get("slippage_bps",5)/10000
            _same(adverse,slip+impact,"modeled_slippage_impact")
            by_symbol[s]["fees"]+=fee
            by_symbol[s]["modeled_slippage"]+=slip
            by_symbol[s]["modeled_impact"]+=impact
            by_symbol[s]["requested_shares"]+=order["requested"]
            by_symbol[s]["filled_shares"]+=q
            if q:
                order_fills[(s,order["side"])]+=q
        for trade in trades[day]:
            s=trade["symbol"]
            q=trade["shares"]
            if trade["side"] not in ("BUY","SELL") or type(q) is not int or q<=0:
                raise ValueError("REPLAY_REVIEW_TRADE_QUANTITY")
            trade_fills[(s,trade["side"])]+=q
            expected[s]=expected.get(s,0)+(q if trade["side"]=="BUY" else -q)
            by_symbol[s]["trade_cost"]+=trade["cost"]
            by_symbol[s]["executed_reference_notional"]+=q*trade["reference_price"]
        if order_fills!=trade_fills or {s:q for s,q in expected.items() if q}!=current:
            raise ValueError("REPLAY_REVIEW_POSITION_RECONCILIATION")
        for s,q in previous.items():
            by_symbol[s]["overnight_pnl"]+=q*(mark["open"][s]-previous_close[s])
        for s,q in current.items():
            by_symbol[s]["intraday_pnl"]+=q*(mark["close"][s]-mark["open"][s])
        for s,v in by_symbol.items():
            _same(v["trade_cost"],v["fees"]+v["modeled_slippage"]+v["modeled_impact"],"symbol_cost")
            v["net_pnl"]=v["overnight_pnl"]+v["intraday_pnl"]-v["trade_cost"]
            for k,value in v.items():
                totals[s][k]+=value
        for k in ("overnight_pnl","intraday_pnl","trade_cost"):
            _same(fsum(v[k] for v in by_symbol.values()),row[k],k)
        net=fsum(v["net_pnl"] for v in by_symbol.values())
        _same(net,row["equity"]-prior_equity,"daily_equity")
        _same(row["equity"],row["settled_cash"]+row["unsettled_cash"]+
              fsum(q*mark["close"][s] for s,q in current.items()),"nav")
        daily.append({"date":day,"net_pnl":net,"symbols":{s:dict(v) for s,v in sorted(by_symbol.items())}})
        previous,previous_close,prior_equity=current,mark["close"],row["equity"]
    _same(fsum(v["net_pnl"] for v in totals.values()),prior_equity-initial,"total_equity")
    _same(fsum(v["trade_cost"] for v in totals.values()),run["total_cost"],"total_cost")
    return {"daily":daily,"by_symbol":{s:dict(v) for s,v in sorted(totals.items())},
            "net_pnl":prior_equity-initial,"reconciled":True,
            "broker_costs_verified":False,"unfilled_opportunity_cost_estimated":False}


def review(payload,run,*,common_days,sectors):
    """Fixed same-policy momentum baseline, doubled costs and half liquidity."""
    if (payload.get("mode")!="synthetic" or payload.get("synthetic_fixture") is not True
            or any(k in payload and payload[k] is not v for k,v in FLAGS.items())
            or "margin_policy" in payload or len(payload["sessions"])>250):
        raise ValueError("REPLAY_REVIEW_SYNTHETIC_ONLY")
    sessions=payload["sessions"]
    dates=[m["date"] for m in sessions]
    common=list(common_days)
    if not common or common!=dates[:len(common)] or len(dates)<3:
        raise ValueError("REPLAY_REVIEW_COMMON_WINDOW")
    universe=sorted(sessions[0]["close"])
    if set(sectors)!=set(universe) or any(set(m["close"])!=set(universe) for m in sessions):
        raise ValueError("REPLAY_REVIEW_FIXED_UNIVERSE")
    baseline=deepcopy(payload)
    baseline["missing_signal_policy"]="hold"
    previous=None
    top=payload.get("top_k",2)
    for m in baseline["sessions"]:
        m["signals"]=[]
        if previous is not None and m["date"] in common:
            values={s:m["close"][s]/previous[s]-1 for s in universe}
            ranked=sorted(universe,key=lambda s:(-values[s],s))
            if len(set(values.values()))>1 and (top==len(ranked) or values[ranked[top-1]]!=values[ranked[top]]):
                stamp=m["close_at"]
                m["signals"]=[{"symbol":s,"security_id":"SYN:"+s,"sector":sectors[s],
                    "model_version":"synthetic_previous_close_momentum_v1","identity_source":"synthetic",
                    "data_source":"synthetic","data_quality":"synthetic_test_only","research_eligible":True,
                    "model_approved_for_training":False,"generated_at":stamp,"available_at":stamp,
                    "data_available_at":stamp,"expires_at":stamp,"factor_score":values[s],"model_rank":i}
                    for i,s in enumerate(ranked,1)]
        previous=m["close"]
    stressed=deepcopy(payload)
    for k in ("fee_bps","slippage_bps"):
        stressed[k]=2*finite(payload.get(k,5),k,minimum=0)
    policy=stressed["execution_policy"]
    policy["impact_bps_at_full_participation"]=2*finite(policy.get("impact_bps_at_full_participation",50),"impact",minimum=0)
    policy["max_participation"]=.5*finite(policy.get("max_participation",.01),"participation",minimum=0)
    if stressed["fee_bps"]+stressed["slippage_bps"]>=1000 or policy["impact_bps_at_full_participation"]>500:
        raise ValueError("REPLAY_REVIEW_STRESS_COST_OUTSIDE_MODEL")
    # Price-only buy-and-hold reference, identical dates, not execution matched.
    levels=[fsum(m["close"][s]/sessions[0]["close"][s] for s in universe)/len(universe) for m in sessions]
    benchmark=[b/a-1 for a,b in zip(levels,levels[1:])]
    results={}
    for name,config,sim in (("model_rank",payload,run),
            ("momentum",baseline,replay(baseline)),("cost_liquidity_stress",stressed,replay(stressed))):
        audit=attribution(config,sim)
        curve=sim["curve"]
        returns=[r["daily_return"] for r in curve[1:]]
        groups=defaultdict(float)
        for s,v in audit["by_symbol"].items():
            groups[sectors[s]]+=v["net_pnl"]
        results[name]={"metrics":metrics(returns,benchmark),"trade_cost":sim["total_cost"],
                       "trade_count":len(sim["trades"]),"risk_halt_latched":sim["risk_halt_latched"],
                       "ending_holdings":curve[-1]["holdings"],"ending_unsettled_cash":curve[-1]["unsettled_cash"],
                       "sector_net_pnl":dict(sorted(groups.items())),"attribution":audit}
    return {**FLAGS,"schema":"atlaspm_synthetic_strategy_review_v1","approved_for_backtest":False,
            "dates":dates,"policy_sha256":digest({"common_days":common,"sectors":sectors,
                "configuration":{k:v for k,v in payload.items() if k!="sessions"},
                "stress":"double_fee_slip_impact_half_participation_v1"}),
            "strategies":results,"price_only_equal_weight_reference":metrics(benchmark,benchmark),
            "baseline":"prior_completed_close_momentum_same_replay_policy",
            "stress":"double_fee_slip_impact_half_participation",
            "winner_selected":False,"production_model_approved":False,
            "warning":"Descriptive holdout diagnostics only; no strategy re-selection or broker TCA."}
