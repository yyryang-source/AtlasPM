"""Fixed synthetic event-to-exit integration fixture, not a live strategy."""
from __future__ import annotations
from copy import deepcopy
from datetime import timedelta
from atlaspm.engine.contracts import Account,timestamp,finite
from atlaspm.engine.autonomous_decision import portfolio_cycle
from atlaspm.engine.unified import _build_paper_inputs
from atlaspm.execution.institutional_batch import propose_multi_asset_batch
from atlaspm.execution.paper_oms import replay_paper_orders
from atlaspm.research.financial_events import FLAGS
from atlaspm.research.training_registry import digest


def _exit_positions(snapshot, marks, sectors):
    """Strict adapter from OMS numeric valuation to whole-share Account.

    No rounding/truncation of fractional shares or silent dropping of other
    asset types. The Account contract and all risk limits remain unchanged.
    """
    positions=[]
    for row in snapshot["positions"]:
        symbol=row["identifier"]
        if row["type"]!="equity" or symbol not in marks or symbol not in sectors:
            raise ValueError("UNSUPPORTED_EXIT_POSITION")
        qty=finite(row["shares"],"exit_shares",minimum=0)
        if qty!=int(qty) or qty>2**53-1:
            raise ValueError("NONINTEGRAL_EXIT_SHARES:"+symbol)
        if qty:
            positions.append({"symbol":symbol,"sector":sectors[symbol],
                              "shares":int(qty),"mark_price":marks[symbol]})
    return positions


def run_fixture(payload):
    """Entry uses the real synthetic portfolio path. Exit is risk-reducing
    and goes through existing exact-leg preflight and OMS, independent of alpha.
    No external model, actual data or broker call.
    """
    if payload.get("mode")!="synthetic" or payload.get("synthetic_fixture") is not True:
        raise ValueError("FIXTURE_ONLY")
    account=Account.from_payload(payload["account"])
    if not account.account_id.startswith("SYN-") or account.kind!="cash":
        raise ValueError("FIXTURE_ACCOUNT_REQUIRED")
    now=timestamp(payload["as_of"],"as_of")
    evidence_time=(now-timedelta(minutes=5)).isoformat()
    rows=[]
    events=[]
    liquidity={}
    for h in account.holdings:
        # Two fixed synthetic inputs, not genuine trained model predictions.
        for model,value in (("fixture_technical",.12),("fixture_fundamental",.10)):
            event={"mode":"synthetic","security_id":"SYN:"+h.symbol,"kind":model,
                   "observed_at":evidence_time,"fixture_return":value}
            events.append(event)
            rows.append({"security_id":"SYN:"+h.symbol,"asset_type":"stock",
                "category":"technical" if model.endswith("technical") else "fundamental",
                "kind":"quantitative_prediction","source":"synthetic","model_version":model,
                "evidence":[{"sha256":digest(event),"available_at":evidence_time,"received_at":evidence_time,
                             "source":"synthetic","rights_status":"synthetic_test_only"}],
                "generated_at":now.isoformat(),"expires_at":(now+timedelta(minutes=30)).isoformat(),
                "horizon_days":365,"expected_return":value,"uncertainty_radius":.02,
                "risk_factors":["synthetic mark risk"],"invalidation":["fixed fixture stop"],
                "synthetic_fixture":True})
        liquidity[h.symbol]={"daily_dollar_volume":1e7,"spread_bps":2,
                            "observed_at":now.isoformat(),"source":"synthetic"}
    entry=portfolio_cycle(payload,rows,weights={"fixture_technical":2,"fixture_fundamental":1},
                          cost_fraction=.03,liquidity=liquidity)
    if entry["decision"]!="SYNTHETIC_ENTRY_REVIEW_COMPLETE":
        return {**FLAGS,"decision":"ENTRY_BLOCKED","entry":entry}
    oms=entry["portfolio"]["paper_oms"]
    snapshot=oms["account_replay"]
    if any(o["status"]!="FILLED" for o in oms["orders"]) or snapshot["unsettled_receivable"] or snapshot["unsettled_payable"]:
        return {**FLAGS,"decision":"ENTRY_NOT_FULLY_FILLED_OR_SETTLED","entry":entry}
    # Fixed next-session synthetic mark shock triggers an independent exit.
    marks={h.symbol:h.price*.95 for h in account.holdings}
    sectors={h.symbol:h.sector for h in account.holdings}
    try:
        positions=_exit_positions(snapshot,marks,sectors)
        exit_account=Account.from_payload({"account_id":account.account_id,"account_type":"cash",
                        "settled_cash":snapshot["settled_cash"],"margin_debt":0,"positions":positions})
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        return {**FLAGS,"decision":"EXIT_BLOCKED","reason":str(exc),"entry":entry}
    if not positions:
        return {**FLAGS,"decision":"NO_POSITION_TO_MONITOR","entry":entry}
    exit_at=now+timedelta(days=3)
    symbols=[p["symbol"] for p in positions]
    constraints=deepcopy(payload["constraints"])
    paper=deepcopy(payload["paper"])
    # No loosening of existing policy; a denied full exit is reported blocked.
    try:
        ledger,plan,journal,_=_build_paper_inputs(exit_account,exit_at,
            {"symbols":symbols,"weights":dict.fromkeys(symbols,0.)},constraints,paper,
            constraints.get("uniform_equity_shock",.2))
        if not plan or any(x["action"]!="SELL_EQUITY" for x in plan["legs"]):
            raise ValueError("EXIT_MUST_BE_SELL_ONLY")
        approval=propose_multi_asset_batch(ledger,plan)
        if approval["decision"]!="ALLOW_PAPER_INTENT":
            return {**FLAGS,"decision":"EXIT_RISK_BLOCKED","entry":entry,"exit_risk":approval}
        events_exit=journal["events"]
        if any(e["type"] in {"REJECT","CANCELED"} for e in events_exit):
            return {**FLAGS,"decision":"EXIT_NOT_FULLY_FILLED","entry":entry}
        for fill in [e for e in events_exit if e["type"]=="FILL"]:
            events_exit.append({"sequence":len(events_exit)+1,"event_id":"SYN-SETTLE-"+fill["execution_id"],
                "timestamp":(exit_at+timedelta(days=1,seconds=len(events_exit))).isoformat(),
                "type":"SETTLE_SALE","leg_id":fill["leg_id"],"execution_id":fill["execution_id"]})
        closed=replay_paper_orders(ledger,plan,journal)
        final=closed["account_replay"]
        if (closed["decision"]!="PAPER_REPLAY_COMPLETED" or any(p.get("shares",0) for p in final["positions"])
            or abs(final["unsettled_receivable"])>.01 or abs(final["net_liquidation_reconciliation_gap"])>.01):
            raise ValueError("EXIT_RECONCILIATION_FAILED")
        entry_cost=account.net_liquidation-snapshot["marked_net_liquidation"]
        mark_pnl=exit_account.net_liquidation-snapshot["marked_net_liquidation"]
        exit_cost=exit_account.net_liquidation-final["marked_net_liquidation"]
        pnl=final["marked_net_liquidation"]-account.net_liquidation
        if abs(pnl-(mark_pnl-entry_cost-exit_cost))>.01:
            raise ValueError("ATTRIBUTION_RECONCILIATION_FAILED")
        return {**FLAGS,"decision":"SYNTHETIC_EVENT_TO_FLAT_COMPLETE","events":events,
                "entry":entry,"monitor":{"reason":"FIXTURE_STOP_5_PERCENT","marks":marks},
                "exit_risk":approval,"exit_oms":closed,"attribution":{"mark_pnl":mark_pnl,
                "entry_cost":entry_cost,"exit_cost":exit_cost,"net_pnl":pnl,"reconciled":True},
                "trained_model_used":False,"broker_side_effects":False}
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        return {**FLAGS,"decision":"EXIT_BLOCKED","reason":str(exc),"entry":entry}


def main():
    import argparse,json
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture",default="examples/unified_portfolio_synthetic.json")
    args=parser.parse_args()
    path=Path(args.fixture)
    if path.stat().st_size>1024*1024:
        raise ValueError("FIXTURE_SIZE_LIMIT")
    result=run_fixture(json.loads(path.read_text(encoding="utf-8")))
    print(json.dumps({"decision":result["decision"],"attribution":result.get("attribution"),
                      "reason":result.get("reason"),**FLAGS},indent=2))
    return 0 if result["decision"]=="SYNTHETIC_EVENT_TO_FLAT_COMPLETE" else 1


if __name__=="__main__":
    raise SystemExit(main())
