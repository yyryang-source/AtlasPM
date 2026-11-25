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


def review_paper_portfolio(capture, raw_summary, identities, research, *, as_of):
    """Read-only actual-account context + existing decision engine.

    Broker-reported marks are diagnostic observations, not independently
    verified execution prices. Never adapt this account to a synthetic input.
    All positions, including those outside the selected research batch, remain
    in the exposure review. Missing data is never treated as a zero position.
    """
    import re
    from datetime import UTC, datetime
    from math import fsum, isfinite
    from atlaspm.execution.ibkr_mapping import _market_value
    from atlaspm.risk.ibkr_margin_monitor import normalize_ibkr_margin_summary

    now = timestamp(as_of, "as_of").astimezone(UTC)
    if now > datetime.now(UTC):
        raise ValueError("FUTURE_PAPER_PORTFOLIO_REVIEW")
    if (not isinstance(capture, dict)
            or capture.get("source") != "local_authenticated_ibkr_gateway"
            or any(capture.get(k) is not True for k in (
                "paper_identity_verified", "gateway_tls_certificate_validated",
                "session_reported_authenticated"))
            or capture.get("live_execution_enabled") is not False
            or capture.get("broker_order_submission_enabled") is not False):
        raise ValueError("AUTHENTICATED_PAPER_CAPTURE_REQUIRED")
    account_id = capture.get("account_id")
    if not isinstance(account_id, str) or not re.fullmatch(r"DU(?:R)?[0-9]{1,20}", account_id):
        raise ValueError("PAPER_ACCOUNT_IDENTITY_REQUIRED")
    raw = capture["raw"]
    account = raw["selected_account"]
    auth = raw["auth_status"]
    if (account.get("type") != "DEMO" or account.get("currency") != "USD"
            or account.get("brokerageAccess") is not True
            or account.get("accountId", account.get("id")) != account_id
            or any(account[k] != account_id for k in ("id","accountId") if k in account)
            or auth.get("authenticated") is not True or auth.get("connected") is not True
            or auth.get("competing") is True):
        raise ValueError("PAPER_ACCOUNT_CONTEXT_MISMATCH")
    age = (now-timestamp(capture["captured_at_utc"], "captured_at")).total_seconds()
    if not 0 <= age <= 120:
        raise ValueError("PAPER_PORTFOLIO_CAPTURE_STALE")
    if (not isinstance(identities, dict) or not 1 <= len(identities) <= 5
            or not isinstance(research, dict) or set(identities) != set(research)):
        raise ValueError("PAPER_RESEARCH_SCOPE")
    by_id = {}
    rows = []
    coverage = {}
    for symbol, identity in identities.items():
        conid = identity.get("conid")
        if (type(conid) is not int or not 1 <= conid <= 2**63-1
                or conid in by_id or identity.get("symbol") != symbol
                or identity.get("currency") != "USD" or identity.get("asset_class") != "STK"
                or identity.get("broker_identity_checked") is not True):
            raise ValueError("PAPER_RESEARCH_IDENTITY_MISMATCH")
        by_id[conid] = symbol
        item = research[symbol]
        checked = item.get("research_contract")
        if checked is not None:
            body = checked["contract"]
            validated = contract(body, as_of=as_of)
            if (body["security_id"] != f"conid:{conid}" or body["asset_type"] not in {"stock","etf"}
                    or body["synthetic_fixture"] is not False or body["kind"] != "objective"
                    or body["source"] != "ibkr" or validated["numeric_eligible"]
                    or validated["research_id"] != checked["research_id"]):
                raise ValueError("PAPER_RESEARCH_CONTRACT_MISMATCH")
            rows.append(body)
        coverage[symbol] = "CURRENT_OBJECTIVE_RESEARCH" if checked else "NO_CURRENT_CONTRACT"
    decision = decide(rows, as_of=as_of, weights={"ibkr_intraday_observation_v1":1},
                      horizon_days=1, cost_fraction=0) if rows else None
    # Zero cost here is not a trading cost assumption: all evidence is
    # objective-only and there must never be a numeric/actionable candidate.
    if decision and any(c["action"] != "NO_TRADE" or c["numeric_count"] != 0
                        for c in decision["candidates"]):
        raise ValueError("UNEXPECTED_PAPER_NUMERIC_DECISION")
    margin = normalize_ibkr_margin_summary(raw_summary, capture, now_utc=now)
    usd = raw["USD_ledger"]["USD"]
    cash = finite(usd["settledcash"], "USD settled cash")
    usd_nlv = finite(usd["netliquidationvalue"], "USD NLV", minimum=.01)
    nlv = finite(margin["margin_summary_usd_claim"]["total_nlv"], "reported account NLV", minimum=.01)
    positions = raw["positions"]
    if (not isinstance(positions, list) or len(positions) > 1000
            or type(capture.get("position_count")) is not int or len(positions) != capture["position_count"]):
        raise ValueError("PAPER_POSITION_SCOPE")
    exposure = []
    held = set()
    for row in positions:
        cid = row.get("conid")
        if type(cid) is not int or not 1 <= cid <= 2**63-1 or cid in held or row.get("currency") != "USD":
            raise ValueError("PAPER_POSITION_IDENTITY_MISMATCH")
        held.add(cid)
        qty = finite(row.get("position"), "reported quantity")
        value = _market_value(row, cid)
        if value is not None and (qty*value < 0 or (qty == 0 and value != 0)):
            raise ValueError("PAPER_POSITION_VALUE_SIGN")
        kind = row.get("assetClass", row.get("secType", "UNKNOWN"))
        selected = by_id.get(cid)
        if selected is not None and kind != "STK":
            raise ValueError("PAPER_HELD_RESEARCH_ASSET_MISMATCH")
        exposure.append({"security_id":f"conid:{cid}", "broker_asset_class":kind,
                         "reported_quantity":qty, "reported_market_value_usd":value,
                         "reported_weight":value/nlv if value is not None else None,
                         "research_coverage":coverage[selected] if selected is not None else "OUTSIDE_SELECTED_BATCH",
                         "position_units_independently_verified":False, "target_weight":None})
    complete = all(p["reported_market_value_usd"] is not None for p in exposure)
    net = fsum(p["reported_market_value_usd"] for p in exposure) if complete else None
    gross = fsum(abs(p["reported_market_value_usd"]) for p in exposure) if complete else None
    bridge = usd_nlv-cash-net if complete else None
    if any(v is not None and not isfinite(v) for v in (net,gross,bridge,cash/nlv)):
        raise ValueError("PAPER_EXPOSURE_NONFINITE")
    blockers = set(margin["data_integrity_block_reasons"])
    blockers.update(margin["risk_alerts"])
    blockers.update({"NO_APPROVED_RETURN_FORECAST", "SECURITY_MASTER_ADMISSION_REQUIRED",
                     "EXECUTION_MARKS_UNVERIFIED", "FINANCING_DEBT_AND_COLLATERAL_UNVERIFIED",
                     "INDEPENDENT_ORDER_RISK_APPROVAL_REQUIRED", "REAL_PORTFOLIO_OPTIMIZER_ADMISSION_REQUIRED"})
    if not complete:
        blockers.add("MISSING_BROKER_POSITION_VALUES")
    if bridge is not None and abs(bridge) > .01:
        blockers.add("USD_CASH_PLUS_MARKS_NLV_GAP_UNEXPLAINED")
    if cash < 0:
        blockers.add("NEGATIVE_SETTLED_CASH_REQUIRES_DEBT_REVIEW")
    return {**FLAGS, "status":"PAPER_PORTFOLIO_REVIEW_COMPLETED",
            "decision":"NO_TRADE", "as_of":as_of, "capture_age_seconds":age,
            "reported_account_nlv_usd":nlv, "reported_usd_currency_nlv":usd_nlv,
            "reported_settled_cash_usd":cash,
            "reported_available_funds_usd":margin["margin_summary_usd_claim"]["available_funds"],
            "reported_net_position_value_usd":net, "reported_gross_position_value_usd":gross,
            "unexplained_usd_cash_mark_gap":bridge, "reported_cash_weight":cash/nlv,
            "reported_gross_position_weight":gross/nlv if gross is not None else None,
            "positions":exposure, "research_coverage":coverage, "research_decision":decision,
            "margin_monitor_state":margin["monitor_state"],
            "nlv_reconciliation_state":margin["account_nlv_reconciliation"]["state"],
            "block_reasons":sorted(blockers), "optimizer_invoked":False,
            "target_weights":None, "orders":[], "trade_authorized":False,
            "portfolio_mark_eligible":False, "ready_for_institutional_risk_gate":False}


def redacted_paper_portfolio_review(report):
    """No balances, complete account IDs, quantities or licensed values on stdout."""
    from collections import Counter
    decision = report["research_decision"]
    return {**FLAGS, "status":report["status"], "decision":report["decision"],
            "capture_age_seconds":report["capture_age_seconds"],
            "position_count":len(report["positions"]),
            "positions_with_broker_values":sum(p["reported_market_value_usd"] is not None for p in report["positions"]),
            "held_research_coverage":dict(Counter(p["research_coverage"] for p in report["positions"])),
            "current_research_count":sum(v=="CURRENT_OBJECTIVE_RESEARCH" for v in report["research_coverage"].values()),
            "decision_candidate_count":len(decision["candidates"]) if decision else 0,
            "numeric_candidate_count":sum(c["numeric_count"] for c in decision["candidates"]) if decision else 0,
            "usd_cash_mark_bridge_status":"UNKNOWN" if report["unexplained_usd_cash_mark_gap"] is None else
                "MATCH" if abs(report["unexplained_usd_cash_mark_gap"]) <= .01 else "UNEXPLAINED",
            "margin_monitor_state":report["margin_monitor_state"],
            "nlv_reconciliation_state":report["nlv_reconciliation_state"],
            "block_reasons":report["block_reasons"], "optimizer_invoked":False,
            "target_weights":None, "order_count":0, "trade_authorized":False,
            "portfolio_mark_eligible":False, "ready_for_institutional_risk_gate":False}


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


def rank_synthetic_baskets(ledger, plans, *, rows, as_of, weights, horizon_days,
                           annual_financing_rate, risk_penalty):
    """Compare exact-leg synthetic candidates, not continuous asset weights.

    Existing institutional preflight owns units, costs, collateral and
    independent risk. Returns are per marked instrument (option premium,
    dirty bond value), not underlying-stock returns. No short option,
    unsupported maturity or unverified execution may gain authority.
    """
    from datetime import timedelta
    from atlaspm.execution.institutional_batch import propose_multi_asset_batch
    if (not isinstance(ledger,dict) or not ledger.get("_synthetic_fixture")
        or not isinstance(plans,list) or not 1<=len(plans)<=8 or
        any(not str(a["account_id"]).startswith("DEMO-") or any(p["mark_source"]!="synthetic"
            for p in a["positions"]) for a in ledger["accounts"])
        or any(not isinstance(p,dict) or not p.get("_synthetic_fixture") for p in plans)):
        raise ValueError("SYNTHETIC_BASKET_FIXTURES_REQUIRED")
    if any(p["account_id"]!=plans[0]["account_id"] for p in plans):
        raise ValueError("BASKET_ACCOUNT_MISMATCH")
    # A comparison must not reward a candidate for weakening its risk policy.
    common=("policy","stress_assumptions","broker_snapshot","allow_margin_borrowing")
    if any(any(p.get(k)!=plans[0].get(k) for k in common) for p in plans):
        raise ValueError("BASKET_RISK_POLICY_MISMATCH")
    if len({p["batch_id"] for p in plans})!=len(plans):
        raise ValueError("DUPLICATE_BASKET_ID")
    if timestamp(ledger["valuation_at"],"valuation_at")!=timestamp(as_of,"as_of"):
        raise ValueError("BASKET_VALUATION_MISMATCH")
    if any(r.get("synthetic_fixture") is not True for r in rows):
        raise ValueError("SYNTHETIC_BASKET_RESEARCH_REQUIRED")
    rate=finite(annual_financing_rate,"financing_rate",minimum=0)
    penalty=finite(risk_penalty,"risk_penalty",minimum=0)
    if rate>1 or penalty>10:
        raise ValueError("BASKET_ASSUMPTION_LIMIT")
    decision=decide(rows,as_of=as_of,weights=weights,horizon_days=horizon_days,cost_fraction=0)
    groups={(g["security_id"],g["asset_type"]):g for g in decision["candidates"]}
    through=(timestamp(as_of,"as_of")+timedelta(days=horizon_days)).date().isoformat()
    results=[]
    for plan in plans:
        approval=propose_multi_asset_batch(ledger,plan)
        row={"batch_id":approval["batch_id"],"plan_sha256":digest(plan),"status":"BLOCKED",
             "reasons":list(approval["block_reasons"]),"score":None}
        projected=approval["pro_forma_unexecuted"]
        if approval["decision"]=="ALLOW_PAPER_INTENT":
            expected=0.
            for p in projected["positions"]:
                sid="SYN:"+p["identifier"]
                kind=p["type"]
                types=("stock","etf") if kind=="equity" else ("option",) if kind=="option" else ("bond",)
                matches=[groups[(sid,a)] for a in types if (sid,a) in groups]
                if len(matches)!=1 or matches[0]["blocked_reasons"]:
                    row["reasons"].append("MISSING_ROBUST_INSTRUMENT_FORECAST")
                    continue
                if kind=="option" and (p["contracts"]<0 or p["expiration_date"]<=through):
                    row["reasons"].append("OPTION_MARGIN_OR_EXPIRY_UNSUPPORTED")
                if kind=="treasury" and (p["maturity_date"]<=through or p["dv01_usd_per_bp"] is None):
                    row["reasons"].append("BOND_MATURITY_OR_DURATION_UNSUPPORTED")
                expected+=p["market_value"]*matches[0]["lower_bound"]
            if not row["reasons"]:
                financing=projected["margin_debt"]*rate*horizon_days/365
                cost=projected["total_fee"]
                stress=projected["net_liquidation"]*projected["conservative_stress_loss_fraction_without_put_credit"]
                row.update(status="EXPERIMENTAL_REVIEW",lower_pnl=expected,
                           financing_cost_assumption=financing,entry_fees=cost,stress_loss=stress,
                           score=expected-financing-cost-penalty*stress)
        results.append(row)
    results.sort(key=lambda r:(r["score"] is None,-(r["score"] or 0),r["batch_id"]))
    chosen=next((r["batch_id"] for r in results if r["score"] is not None and r["score"]>0),None)
    return {**FLAGS,"candidates":results,"research_candidate":chosen,
            "trade_authorized":False,"weights_validated":False,"decision":"REVIEW_ONLY" if chosen else "NO_TRADE"}
