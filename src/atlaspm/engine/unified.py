"""Unified Portfolio Engine V1: synthetic, fail-closed portfolio decision loop.

End-to-end OMS uses the existing institutional ledger, basket risk gateway and
paper event replayer. Synthetic preview values are local test doubles ONLY,
never verified IBKR or production data. No market database is read or written.
"""
from __future__ import annotations

from datetime import timedelta
import logging
from math import ceil, floor, sqrt
from typing import Any, Mapping

from atlaspm.engine.allocation import optimize_target
from atlaspm.engine.analytics import build_analytics
from atlaspm.engine.contracts import Account, Candidate, finite, object_map, timestamp, validate_candidates
from atlaspm.execution.institutional_batch import propose_multi_asset_batch
from atlaspm.execution.institutional_ledger import build_institutional_ledger
from atlaspm.execution.paper_oms import replay_paper_orders
from atlaspm.engine.mean_variance import _validate_covariance
from atlaspm.risk.risk_budget import check_risk_budget

LOGGER = logging.getLogger(__name__)

SAFETY = {
    "research_only": True,
    "live_execution_enabled": False,
    "broker_order_submission_enabled": False,
    "approved_for_model_training": False,
    "broker_margin_verified": False,
    "historical_security_identity_verified": False,
}


def reduction_research(account: Account) -> list[dict[str, Any]]:
    """Independent sell-only research proposals; no strategy/optimizer permissions needed."""
    return [{"symbol": h.symbol, "side": "SELL", "max_research_quantity": h.shares,
             "reason": "INDEPENDENT_DE_RISK_RESEARCH_ONLY_NO_ORDER_AUTHORITY"}
            for h in account.holdings]


def _blocked(reason: str, account: Account | None = None, **extra: Any) -> dict[str, Any]:
    LOGGER.info("Synthetic portfolio decision BLOCK: %s", reason)
    return {
        **SAFETY, "method": "unified_portfolio_engine_v1",
        "decision": "BLOCK", "block_reasons": [reason],
        "independent_reduction_research": reduction_research(account) if account else [],
        **extra,
    }


def _risk_gate(candidates: list[Candidate], account: Account,
               allocation: dict[str, Any], market: Mapping[str, Any],
               constraints: Mapping[str, Any], history: object) -> dict[str, Any]:
    symbols = allocation["symbols"]
    weights = allocation["weights"]
    cov = _validate_covariance(market.get("covariance"), len(symbols))
    w = [weights[s] for s in symbols]
    risky = sum(w)
    volatility = sqrt(max(0., sum(w[i]*cov[i][j]*w[j]
                                   for i in range(len(w)) for j in range(len(w)))))
    sector_exposure: dict[str, float] = {}
    for c in candidates:
        sector_exposure[c.sector] = sector_exposure.get(c.sector, 0.) + weights[c.symbol]
    blockers: list[str] = []
    warnings: list[str] = []
    annual_vol_cap = finite(constraints.get("max_annual_volatility", .7), "max_annual_volatility", minimum=0)
    if volatility > annual_vol_cap+1e-8:
        blockers.append("PORTFOLIO_VOLATILITY_LIMIT")
    shock = finite(constraints.get("uniform_equity_shock", .2), "uniform_equity_shock", minimum=0)
    shock_cap = finite(constraints.get("max_stress_loss_fraction", .5), "max_stress_loss_fraction", minimum=0)
    if shock > 1 or shock_cap > 1:
        raise ValueError("INVALID_RISK_SHOCK_OR_CAP")
    if risky*shock > shock_cap+1e-8:
        blockers.append("STRESS_LOSS_BUDGET_EXCEEDED")
    gross = account.market_value / account.net_liquidation
    margin: dict[str, Any] = {
        "account_type": account.kind, "starting_equity": account.net_liquidation,
        "starting_margin_debt": account.debt, "starting_gross_leverage": gross,
        "starting_net_leverage": (account.market_value-account.debt)/account.net_liquidation,
        "annual_financing_interest_estimate": account.debt * float(
            constraints.get("annual_margin_rate", .065)),
        "buying_power_verified": False, "per_security_requirements_verified": False,
        "scenario_maintenance_shortfall": None,
    }
    requirement_inputs = market.get("margin_requirements")
    if account.kind == "margin":
        # No user-controlled bool is sufficient to verify actual broker rules.
        warnings.append("BROKER_MARGIN_AND_BUYING_POWER_NOT_INDEPENDENTLY_VERIFIED")
        if not isinstance(requirement_inputs, dict) or set(requirement_inputs) != set(symbols):
            blockers.append("MARGIN_PER_SECURITY_REQUIREMENTS_UNKNOWN")
        else:
            fractions: dict[str, float] = {}
            for s in symbols:
                raw = object_map(requirement_inputs[s], "margin_requirement")
                initial = finite(raw.get("initial"), f"initial.{s}", minimum=0)
                maintenance = finite(raw.get("maintenance"), f"maintenance.{s}", minimum=0)
                if not 0 < maintenance <= initial <= 1:
                    raise ValueError("INVALID_MARGIN_REQUIREMENT")
                fractions[s] = maintenance
            after_value = sum(weights[s]*account.net_liquidation for s in symbols)
            stressed_equity = account.net_liquidation - after_value*shock
            stressed_maintenance = sum(weights[s]*account.net_liquidation*(1-shock)*fractions[s]
                                       for s in symbols)
            margin["scenario_maintenance_shortfall"] = max(0.,stressed_maintenance-stressed_equity)
            margin["scenario_stressed_equity"] = stressed_equity
            margin["scenario_stressed_maintenance"] = stressed_maintenance
            if stressed_equity < stressed_maintenance:
                blockers.append("MARGIN_STRESS_MAINTENANCE_SHORTFALL")
        # Always deny automated increases in margin buying power until a trusted
        # independent broker adapter verifies debt, identity and all requirements.
        starting = {h.symbol: h.shares*h.price for h in account.holdings}
        if any(weights[s]*account.net_liquidation > starting.get(s, 0.)+1e-6 for s in symbols):
            blockers.append("UNVERIFIED_MARGIN_BLOCKS_RISK_ADDITIONS")

    betas = market.get("betas")
    beta = None
    if isinstance(betas, dict) and set(betas) == set(symbols):
        beta = sum(weights[s]*finite(betas[s], f"beta.{s}") for s in symbols)
        if abs(beta) > finite(constraints.get("max_absolute_beta", 10.), "max_absolute_beta", minimum=0):
            blockers.append("BETA_EXPOSURE_LIMIT")
    else:
        warnings.append("BETA_EXPOSURE_UNAVAILABLE_WITHOUT_ALIGNED_ESTIMATES")
        if "max_absolute_beta" in constraints:
            blockers.append("BETA_DATA_REQUIRED_FOR_LIMIT")
    correlations = []
    for i in range(len(w)):
        for j in range(i):
            correlations.append(cov[i][j]/sqrt(cov[i][i]*cov[j][j]))
    max_correlation = max(correlations) if correlations else None
    if max_correlation is not None and max_correlation > finite(
        constraints.get("max_pairwise_correlation", 1.), "max_pairwise_correlation"):
        blockers.append("CORRELATION_LIMIT")

    scenarios = market.get("scenario_returns")
    var95 = cvar95 = None
    if isinstance(scenarios, list) and len(scenarios) >= 20:
        losses: list[float] = []
        for row in scenarios:
            if not isinstance(row, list) or len(row) != len(w):
                raise ValueError("SCENARIO_RETURN_SHAPE_MISMATCH")
            losses.append(-sum(w[i]*finite(r, "scenario_return") for i,r in enumerate(row)))
        tail = sorted(losses, reverse=True)
        k = max(1, ceil(.05*len(tail)))
        var95, cvar95 = tail[k-1], sum(tail[:k])/k
        if cvar95 > finite(constraints.get("max_historical_cvar", 1.), "max_historical_cvar"):
            blockers.append("HISTORICAL_CVAR_LIMIT")
    else:
        warnings.append("HISTORICAL_VAR_CVAR_UNAVAILABLE_WITHOUT_20_ALIGNED_OBSERVATIONS")
        if "max_historical_cvar" in constraints:
            blockers.append("HISTORICAL_CVAR_DATA_REQUIRED")

    drawdown = None
    if isinstance(history, dict) and isinstance(history.get("portfolio"), list):
        path, peak, dd = 1., 1., 0.
        for r in history["portfolio"]:
            path *= 1+finite(r,"history.portfolio_return")
            if path <= 0:
                raise ValueError("NONPOSITIVE_RESEARCH_EQUITY_INDEX")
            peak = max(peak,path)
            dd = min(dd,path/peak-1)
        drawdown = dd
        if abs(dd) > finite(constraints.get("max_historical_drawdown", 1.), "max_historical_drawdown"):
            blockers.append("MAX_DRAWDOWN_LIMIT")
    elif "max_historical_drawdown" in constraints:
        blockers.append("DRAWDOWN_HISTORY_REQUIRED")

    # Reuse the original risk-budget gate on the risky sleeve. It assumes
    # fully invested long-only and uses a conservative 1x gross-risk proxy.
    # A deliberately cash-only synthetic target has no normalized risky
    # sleeve. This is not a waiver of other independent risk checks.
    base_risk = None if risky<=1e-12 else check_risk_budget(
        {s: weights[s]/risky for s in symbols}, cov,
        proposed_leverage=1.0, max_allowed_leverage=1.0,
        max_name_weight=1.0, max_annual_volatility=max(annual_vol_cap/max(risky,1e-8), .00001),
        stress_decline=shock, max_stress_loss_fraction=1.,
        annual_financing_rate=0., max_annual_financing_cost_fraction=1.,
    )
    margin_rate_shock = finite(constraints.get("annual_rate_shock", .03), "annual_rate_shock", minimum=0)
    liquidity_haircut = finite(constraints.get("liquidity_haircut", .1), "liquidity_haircut", minimum=0)
    margin["annual_interest_rate_upshock_cost"] = account.debt*(
        float(constraints.get("annual_margin_rate", .065))+margin_rate_shock)
    margin["liquidity_haircut_equity_loss"] = risky*liquidity_haircut*account.net_liquidation
    named = market.get("historical_scenarios", {})
    if not isinstance(named, dict):
        raise ValueError("historical_scenarios must be a mapping")
    scenario_results: dict[str, float] = {}
    for regime in ("2008", "2020", "2022"):
        if regime not in named:
            continue
        row = object_map(named[regime], "historical_scenario")
        if set(row) != set(symbols):
            raise ValueError(f"HISTORICAL_SCENARIO_SYMBOL_MISMATCH:{regime}")
        scenario_results[regime] = sum(weights[s]*finite(row[s], f"scenario.{regime}.{s}")
                                       for s in symbols)
    if "max_named_scenario_loss_fraction" in constraints:
        if set(scenario_results) != {"2008","2020","2022"}:
            blockers.append("HISTORICAL_SCENARIOS_REQUIRED_FOR_LIMIT")
        elif min(scenario_results.values()) < -finite(
                constraints["max_named_scenario_loss_fraction"],
                "max_named_scenario_loss_fraction", minimum=0):
            blockers.append("NAMED_STRESS_SCENARIO_LIMIT")
    return {
        "decision": "BLOCK" if blockers else "PASS_SYNTHETIC_RESEARCH",
        "block_reasons": sorted(set(blockers)),
        "warnings": warnings,
        "annual_volatility": volatility, "sector_exposures": sector_exposure,
        "max_pairwise_correlation": max_correlation, "beta_exposure": beta,
        "historical_var_95": var95, "historical_cvar_95": cvar95,
        "historical_max_drawdown": drawdown,
        "illustrative_uniform_shock_loss_fraction": risky*shock,
        "historical_named_regimes_available": False,
        "user_supplied_regime_shocks_illustrative": scenario_results,
        "historical_scenario_status": "SUPPLIED_SCENARIOS_ARE_UNVERIFIED_NOT_HISTORICAL_REPLAY",
        "stylized_equity_shocks_not_historical_backtests": {
            "2008_like": -.55*risky, "2020_like": -.34*risky,
            "2022_like": -.25*risky,
        },
        "margin": margin,
        "legacy_risk_budget_reference": base_risk,
    }


def _build_paper_inputs(account: Account, as_of: Any, allocation: Mapping[str, Any],
                        constraints: Mapping[str, Any], paper: Mapping[str, Any],
                        shock: float) -> tuple[dict[str, Any], dict[str, Any],
                                                 dict[str, Any], dict[str, Any]]:
    equity = account.net_liquidation
    fee = finite(paper.get("fee_per_order", 1.), "fee_per_order", minimum=0)
    slip = finite(paper.get("slippage_bps", 0.), "slippage_bps", minimum=0)
    impact = finite(paper.get("market_impact_bps", 0.), "market_impact_bps", minimum=0)
    fill_fraction = finite(paper.get("fill_fraction", 1.), "fill_fraction", minimum=0)
    if slip + impact > 20 or fill_fraction > 1:
        raise ValueError("UNSUPPORTED_SLIPPAGE_OR_FILL_FRACTION")
    minimum = finite(constraints.get("min_trade_notional", 100.), "min_trade_notional", minimum=0)
    holdings = {h.symbol: h for h in account.holdings}
    if set(allocation["symbols"]) - set(holdings):
        raise ValueError("OMS_UNHELD_EQUITY_UNSUPPORTED")
    max_ticket = finite(constraints.get("max_single_order_fraction", 1.), "max_single_order_fraction", minimum=0)
    legs: list[dict[str, Any]] = []
    after = {h.symbol:h.shares for h in account.holdings}
    buy_cost = 0.
    nlv_delta = 0.
    expected_sale_receivable = 0.
    for symbol in allocation["symbols"]:
        h = holdings[symbol]
        desired = equity*allocation["weights"][symbol]/h.price
        # Conservative round: no overspending and no excess risky exposure.
        quantity = floor(desired + 1e-9) - h.shares
        if quantity == 0:
            continue
        side = "BUY_EQUITY" if quantity > 0 else "SELL_EQUITY"
        units = abs(quantity)
        price = round(h.price*(1 + ((slip+impact)/10000 if quantity > 0 else -(slip+impact)/10000)), 8)
        if price <= 0:
            raise ValueError("INVALID_EXECUTION_PRICE")
        notional = units*price
        if notional < minimum-1e-8:
            raise ValueError(f"BELOW_MINIMUM_TRADE_NOTIONAL:{symbol}")
        if notional/equity > max_ticket+1e-8:
            raise ValueError(f"MAX_SINGLE_ORDER_FRACTION:{symbol}")
        leg = {"leg_id": f"SIM-{len(legs)+1}-{symbol}-{side}", "action": side,
               "identifier": symbol, "shares": units, "limit_price": price, "fee": fee}
        legs.append(leg)
        after[symbol] += quantity
        nlv_delta += units*(h.price-price if quantity>0 else price-h.price)-fee
        if quantity > 0:
            buy_cost += notional+fee
        else:
            expected_sale_receivable += notional-fee
    if len(legs) > 20:
        raise ValueError("BATCH_MAX_20_LEGS")
    if not legs:
        return {}, {}, {}, {"notional":0., "fees":0., "slippage":0., "positions":after}
    if buy_cost > account.cash+1e-8:
        raise ValueError("UNSETTLED_SALES_CANNOT_FINANCE_BUYS")
    after_nlv = equity+nlv_delta
    after_equity_mv = sum(after[s]*h.price for s,h in holdings.items())
    after_cash = account.cash - buy_cost
    min_cash = finite(constraints.get("min_cash_fraction", .1), "min_cash_fraction", minimum=0)
    if after_cash/after_nlv < min_cash-1e-8:
        raise ValueError("POST_TRADE_CASH_RESERVE_BREACH")
    cap = finite(constraints.get("max_name_weight", .6), "max_name_weight", minimum=0)
    for s,h in holdings.items():
        if after[s]*h.price/after_nlv > cap+1e-6:
            raise ValueError(f"POST_ROUNDING_NAME_CAP:{s}")
    sector_value: dict[str,float] = {}
    for s,h in holdings.items():
        sector_value[h.sector] = sector_value.get(h.sector,0.)+after[s]*h.price
    sector_limits = constraints.get("sector_limits",{})
    for s,v in sector_value.items():
        if v/after_nlv > float(sector_limits.get(s,1.))+1e-6:
            raise ValueError(f"POST_ROUNDING_SECTOR_CAP:{s}")
    if after_equity_mv/after_nlv > finite(
        constraints.get("max_gross_exposure",1.), "max_gross_exposure")+1e-6:
        raise ValueError("POST_ROUNDING_GROSS_EXPOSURE")
    actual_turnover = sum(abs(after[s]-h.shares)*h.price for s,h in holdings.items())/equity
    if actual_turnover > finite(constraints.get("max_turnover",2.), "max_turnover")+1e-6:
        raise ValueError("POST_ROUNDING_TURNOVER")
    tick = as_of.isoformat()
    ledger = {"valuation_at":tick, "max_mark_age_seconds":120,
              "reconciliation_tolerance":.01,
              "accounts":[{"account_id":account.account_id,
                           "account_type":account.kind,"currency":"USD",
                           "snapshot_at":tick,"settled_cash":account.cash,
                           "unsettled_receivable":0.,"unsettled_payable":0.,
                           "margin_debt":account.debt,
                           "accrued_financing_interest":account.accrued_interest,
                           "broker_reported_net_liquidation":equity,
                           "positions":[{"type":"equity","identifier":h.symbol,
                                         "shares":h.shares,"mark_price":h.price,
                                         "mark_source":"synthetic",
                                         "mark_age_seconds":0} for h in account.holdings]}]}
    gross = after_equity_mv/after_nlv
    risk_stress = after_equity_mv*shock/after_nlv
    policy = {
        "max_data_age_seconds":120, "max_gross_leverage": max(1.,min(4.,float(
            constraints.get("max_gross_exposure",1.)))),
        "max_stress_loss_fraction":float(constraints.get("max_stress_loss_fraction",.5)),
        "min_excess_liquidity_fraction":0., "max_single_order_fraction":max_ticket,
        "max_option_premium_fraction":0., "watch_drawdown_fraction":.1,
        "reduce_drawdown_fraction":.2, "critical_drawdown_fraction":.3,
        "max_limit_mark_deviation_bps":20.,
    }
    # Synthetic preview is intentionally generated only for an explicitly
    # synthetic test fixture. It must never be characterized as broker-verified.
    batch_id = "SYN-UNIFIED-" + account.account_id
    plan = {"batch_id":batch_id,"account_id":account.account_id,
            "allow_margin_borrowing":False,"legs":legs,"policy":policy,
            "broker_snapshot":{"account_id":account.account_id,"net_liquidation":equity,
                               "margin_debt":account.debt,
                               "available_funds":account.cash,
                               "excess_liquidity":equity,"data_age_seconds":0},
            "market":{"oldest_quote_age_seconds":0},
            "risk_observation":{"current_drawdown_fraction":0.,
                                "pro_forma_stress_loss_fraction":risk_stress,
                                "pro_forma_gross_leverage":gross},
            "stress_assumptions":{"equity_decline_fraction":shock,
                                  "treasury_decline_fraction":0.},
            "basket_preview":{"batch_id":batch_id,"account_id":account.account_id,
                              "order_legs":legs,"accepted":True,
                              "age_seconds":0,"available_funds_after":after_cash,
                              "excess_liquidity_after":after_nlv,
                              "margin_debt_after":account.debt,
                              "net_liquidation_after":after_nlv}}
    events: list[dict[str, Any]] = []
    for leg in legs:
        leg_id = leg["leg_id"]
        for event_type in ("SUBMIT","ACK"):
            events.append({"sequence":len(events)+1,
                           "event_id":f"SYN-{len(events)+1}",
                           "timestamp":(as_of+timedelta(seconds=len(events)+1)).isoformat(),
                           "type":event_type,"leg_id":leg_id})
        filled_qty = min(leg["shares"],floor(leg["shares"]*fill_fraction))
        if filled_qty > 0:
            events.append({"sequence":len(events)+1,
                           "event_id":f"SYN-{len(events)+1}",
                           "timestamp":(as_of+timedelta(seconds=len(events)+1)).isoformat(),
                           "type":"FILL", "leg_id":leg_id,
                           "execution_id":f"SYN-FILL-{leg_id}",
                           "quantity":filled_qty,"price":leg["limit_price"],
                           "fee":round(leg["fee"]*filled_qty/leg["shares"],8)})
        if filled_qty < leg["shares"]:
            if fill_fraction == 0:
                events.append({"sequence":len(events)+1,
                               "event_id":f"SYN-{len(events)+1}",
                               "timestamp":(as_of+timedelta(seconds=len(events)+1)).isoformat(),
                               "type":"REJECT", "leg_id":leg_id})
            else:
                for et in ("CANCEL_REQUEST","CANCELED"):
                    events.append({"sequence":len(events)+1,
                                   "event_id":f"SYN-{len(events)+1}",
                                   "timestamp":(as_of+timedelta(seconds=len(events)+1)).isoformat(),
                                   "type":et,"leg_id":leg_id})
    if paper.get("duplicate_last_event") is True:
        events.append(dict(events[-1]))
    return ledger,plan,{"batch_id":batch_id,"account_id":account.account_id,
                        "events":events},{"notional":sum(leg["shares"]*leg["limit_price"]
                                                   for leg in legs),
                                          "fees":sum(x["fee"] for x in legs),
                                          "slippage":sum(leg["shares"]*abs(
                                              holdings[leg["identifier"]].price-leg["limit_price"])
                                              for leg in legs),"positions":after}


def run_unified_portfolio(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Run an entirely local, deterministic synthetic simulation.

    Raises no live/trading privileges. Invalid requests return explicit BLOCK
    reasons with independent de-risk research available when account is valid.
    """
    account: Account | None = None
    try:
        data = object_map(payload, "payload")
        for flag, required in SAFETY.items():
            if flag in data and data[flag] != required:
                raise ValueError(f"SAFETY_PERMISSION_OVERRIDE_DENIED:{flag}")
        if data.get("mode") != "synthetic" or data.get("synthetic_fixture") is not True:
            raise ValueError("V1_REQUIRES_EXPLICIT_SYNTHETIC_FIXTURE")
        as_of = timestamp(data.get("as_of"), "as_of")
        account = Account.from_payload(object_map(data.get("account"), "account"))
        if "broker_reported_net_liquidation" in data["account"] and abs(
            finite(data["account"]["broker_reported_net_liquidation"],"reported_nlv")
            -account.net_liquidation)>0.01:
            raise ValueError("ACCOUNT_LEDGER_RECONCILIATION_FAILURE")
        market = object_map(data.get("market"),"market")
        constraints = object_map(data.get("constraints", {}),"constraints")
        paper = object_map(data.get("paper",{}),"paper")
        history = data.get("history",{})
        max_age = finite(constraints.get("max_signal_data_age_seconds",86400.),
                         "max_signal_data_age_seconds",minimum=0)
        candidates = validate_candidates(data.get("signals"),as_of=as_of,
                                          max_age_seconds=max_age)
        if any(c.symbol not in {h.symbol for h in account.holdings} for c in candidates):
            raise ValueError("OMS_UNHELD_EQUITY_UNSUPPORTED")
        allocation = optimize_target(candidates,account,market,constraints)
        gate = _risk_gate(candidates,account,allocation,market,constraints,history)
        if gate["block_reasons"]:
            return _blocked("RISK_GATE_BLOCKED",account,allocation=allocation,
                            risk=gate,block_reasons=gate["block_reasons"])
        ledger,plan,events,statistics = _build_paper_inputs(
            account,as_of,allocation,constraints,paper,
            float(constraints.get("uniform_equity_shock",.2)))
        if not plan:
            return {**SAFETY,"method":"unified_portfolio_engine_v1",
                    "decision":"NO_REBALANCE_NEEDED","block_reasons":[],
                    "allocation":allocation,"risk":gate,
                    "independent_reduction_research":reduction_research(account)}
        # Reuse institutional ledger, exact-leg basket risk checks and original OMS.
        institutional_ledger = build_institutional_ledger(ledger)
        preflight = propose_multi_asset_batch(ledger,plan)
        if preflight["decision"] != "ALLOW_PAPER_INTENT":
            return _blocked("EXISTING_INSTITUTIONAL_RISK_GATE_BLOCKED",account,
                            allocation=allocation,risk=gate,
                            block_reasons=preflight["block_reasons"])
        replay = replay_paper_orders(ledger,plan,events)
        actual = replay["account_replay"]
        # Independent reconciliation against OMS fills, not target positions:
        fills = replay["fill_records"]
        expected_shares = {h.symbol:h.shares for h in account.holdings}
        spent = 0.
        receivable = 0.
        fees_paid = 0.
        slip_paid = 0.
        impact_paid = 0.
        for fill in fills.values():
            leg = next(p for p in plan["legs"] if p["leg_id"] == fill["leg_id"])
            symbol = leg["identifier"]
            qty, px, fee_paid = fill["quantity"],fill["price"],fill["fee"]
            fees_paid += fee_paid
            original_price = next(h.price for h in account.holdings if h.symbol == symbol)
            slip_paid += qty*original_price*finite(paper.get("slippage_bps",0.),"slippage_bps",minimum=0)/10000
            impact_paid += qty*original_price*finite(paper.get("market_impact_bps",0.),"market_impact_bps",minimum=0)/10000
            if fill["action"] == "BUY_EQUITY":
                expected_shares[symbol] += qty
                spent += qty*px+fee_paid
            else:
                expected_shares[symbol] -= qty
                receivable += qty*px-fee_paid
        position_map = {p["identifier"]:p["shares"]
                        for p in actual["positions"] if p["type"] == "equity"}
        position_map = {s:position_map.get(s,0) for s in expected_shares}
        checks = {
            "shares":position_map == expected_shares,
            "settled_cash":abs(actual["settled_cash"]-(account.cash-spent))<.01,
            "unsettled_sale_receivables":abs(actual["unsettled_receivable"]-receivable)<.01,
            "margin_debt_unchanged":abs(actual["margin_debt"]-account.debt)<.01,
            "account_equity_identity":abs(actual["net_liquidation_reconciliation_gap"])<.01,
            "order_audit_chain":replay["audit_hash_chain_self_consistent"],
            "execution_uniqueness":replay["duplicate_events_ignored"] == (
                1 if paper.get("duplicate_last_event") is True else 0),
        }
        if not all(checks.values()) or replay["decision"] != "PAPER_REPLAY_COMPLETED":
            return _blocked("PAPER_OMS_OR_INDEPENDENT_RECONCILIATION_FAILED",account,
                            allocation=allocation,risk=gate,reconciliation_checks=checks,
                            paper_oms=replay)
        weights = allocation["weights"]
        cov = _validate_covariance(market["covariance"],len(weights))
        names = list(weights)
        factor_exposure = {c.symbol:weights[c.symbol]*c.score for c in candidates}
        variance_contributions = {
            s:weights[s]*sum(cov[i][j]*weights[names[j]] for j in range(len(names)))
            for i,s in enumerate(names)
        }
        analytics = build_analytics(
            history, gross_notional=sum(fill["quantity"]*fill["price"]
                                        for fill in fills.values()),
            equity=account.net_liquidation,
            order_fees=fees_paid,slippage_cost=slip_paid,market_impact_cost=impact_paid,
            annual_interest_assumption=finite(
                constraints.get("annual_margin_rate",.065),"annual_margin_rate",minimum=0),
            margin_debt=account.debt,factor_scores=factor_exposure,
            variance_contributions=variance_contributions,
        )
        LOGGER.info("Synthetic portfolio loop completed; account=%s fills=%d",
                    account.account_id, len(fills))
        return {
            **SAFETY,"method":"unified_portfolio_engine_v1",
            "decision":"SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE",
            "block_reasons":[],"account_type":account.kind,
            "account_net_liquidation_before":account.net_liquidation,
            "signal_count":len(candidates),"allocation":allocation,
            "risk":gate,"generated_order_intents":plan["legs"],
            "institutional_ledger":institutional_ledger,
            "institutional_preflight":preflight,
            "paper_oms":replay,"reconciliation_checks":checks,
            "analytics":analytics,
            "limitations":["synthetic security IDs, not historically verified",
                           "self-generated what-if is not a broker What-If response",
                           "no new tickers in existing Paper OMS",
                           "not a point-in-time validated or tradable backtest",
                           "no broker margin verification or real order authority"],
        }
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return _blocked(str(exc),account)
