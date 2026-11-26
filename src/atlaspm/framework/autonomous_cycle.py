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
    return complete_synthetic_exit(payload, entry, events=events,
        marks={h.symbol:h.price*.95 for h in account.holdings},
        exit_at=now+timedelta(days=3), exit_reason="FIXTURE_STOP_5_PERCENT")


def complete_synthetic_exit(payload, entry, *, events, marks, exit_at,
                            exit_reason, trained_model_used=False):
    """Reuse the existing independent sell-only preflight, OMS and attribution.

    The caller supplies an already completed synthetic entry and explicit later
    marks; no broker or external service is used. This internal fixture helper
    requires the upstream entry to have passed its existing gates.
    """
    if (payload.get("mode")!="synthetic" or payload.get("synthetic_fixture") is not True
        or any(k in payload and payload[k] is not v for k,v in FLAGS.items())
        or entry.get("decision")!="SYNTHETIC_ENTRY_REVIEW_COMPLETE"
        or entry.get("live_execution_enabled") is not False
        or entry.get("broker_order_submission_enabled") is not False
        or type(trained_model_used) is not bool):
        raise ValueError("SYNTHETIC_COMPLETED_ENTRY_REQUIRED")
    account=Account.from_payload(payload["account"])
    if account.kind!="cash" or not account.account_id.startswith("SYN-"):
        raise ValueError("FIXTURE_ACCOUNT_REQUIRED")
    now=timestamp(payload["as_of"],"as_of")
    exit_at=timestamp(exit_at.isoformat(),"exit_at")
    if exit_at<=now or set(marks)!={h.symbol for h in account.holdings}:
        raise ValueError("INVALID_EXIT_TIME_OR_MARK_COVERAGE")
    marks={s:finite(value,"exit_mark",minimum=.000001) for s,value in marks.items()}
    oms=entry["portfolio"]["paper_oms"]
    snapshot=oms["account_replay"]
    if any(o["status"]!="FILLED" for o in oms["orders"]) or snapshot["unsettled_receivable"] or snapshot["unsettled_payable"]:
        return {**FLAGS,"decision":"ENTRY_NOT_FULLY_FILLED_OR_SETTLED","entry":entry}
    sectors={h.symbol:h.sector for h in account.holdings}
    try:
        positions=_exit_positions(snapshot,marks,sectors)
        exit_account=Account.from_payload({"account_id":account.account_id,"account_type":"cash",
                        "settled_cash":snapshot["settled_cash"],"margin_debt":0,"positions":positions})
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        return {**FLAGS,"decision":"EXIT_BLOCKED","reason":str(exc),"entry":entry}
    if not positions:
        return {**FLAGS,"decision":"NO_POSITION_TO_MONITOR","entry":entry}
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
                "entry":entry,"monitor":{"reason":exit_reason,"marks":marks},
                "exit_risk":approval,"exit_oms":closed,"attribution":{"mark_pnl":mark_pnl,
                "entry_cost":entry_cost,"exit_cost":exit_cost,"net_pnl":pnl,"reconciled":True},
                "trained_model_used":trained_model_used,"broker_side_effects":False}
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        return {**FLAGS,"decision":"EXIT_BLOCKED","reason":str(exc),"entry":entry}


def multiasset_fixture(*, checkpoint, event_count=None, scenario="normal"):
    """Persist validated synthetic OMS journal and actual account/order state.

    Fixed packaged basket only. Reuses exact-leg independent preflight and
    OMS; not a real account recovery API or a second order system.
    """
    import json
    from pathlib import Path
    from atlaspm.research.event_queue import _transaction
    root=Path(__file__).resolve().parents[3]/"examples"
    ledger,plan,journal=[json.loads((root/name).read_text(encoding="utf-8"))
        for name in ("institutional_batch_ledger.json","institutional_batch_plan.json","paper_oms_events.json")]
    all_events=journal["events"]
    if scenario in {"cancel","unknown"}:
        all_events=all_events[:3]+[{"sequence":4,"event_id":"SYN-CANCEL-REQUEST",
            "timestamp":"2026-11-20T21:04:00Z","type":"CANCEL_REQUEST","leg_id":"SELL-QQQ-10"},
            {"sequence":5,"event_id":"SYN-CANCEL-RESULT","timestamp":"2026-11-20T21:05:00Z",
             "type":"CANCELED" if scenario=="cancel" else "UNKNOWN_OUTCOME","leg_id":"SELL-QQQ-10"}]
    elif scenario!="normal":
        raise ValueError("INVALID_OMS_FIXTURE_SCENARIO")
    size=len(all_events) if event_count is None else event_count
    if type(size) is not int or not 1<=size<=len(all_events):
        raise ValueError("INVALID_OMS_FIXTURE_PREFIX")
    if not str(checkpoint).endswith(".oms-fixture.json"):
        raise ValueError("OMS_FIXTURE_CHECKPOINT_SUFFIX_REQUIRED")
    journal["events"]=all_events[:size]
    input_hash=digest([ledger,plan,scenario])
    def commit(items):
        if set(items)-{"oms_fixture"}:
            raise ValueError("OMS_FIXTURE_CHECKPOINT_KIND_MISMATCH")
        old=items.get("oms_fixture")
        if old:
            if old["input_sha256"]!=input_hash or journal["events"][:len(old["events"])]!=old["events"]:
                raise ValueError("OMS_FIXTURE_PREFIX_CONFLICT")
            prior=replay_paper_orders(ledger,plan,{**journal,"events":old["events"]})
            if (digest(prior)!=old["result_sha256"] or prior["account_replay"]!=old["account"]
                or prior["orders"]!=old["orders"]):
                raise ValueError("OMS_FIXTURE_STATE_RECONCILIATION_FAILED")
        report=replay_paper_orders(ledger,plan,journal)
        result_hash=digest(report)
        items["oms_fixture"]={"input_sha256":input_hash,"events":journal["events"],
            "result_sha256":result_hash,"account":report["account_replay"],"orders":report["orders"],
            "audit_root_sha256":report["audit_root_sha256"]}
        return {**FLAGS,"mode":"synthetic_multiasset_fixture","events":size,
                "checkpoint_reused":bool(old and old["events"]==journal["events"]),
                "result_sha256":result_hash,"report":report,"broker_side_effects":False}
    return _transaction(checkpoint,commit)


def settlement_fixture():
    """Canonical long-option and nominal Treasury lifecycle integration.

    In-memory generated synthetic CSVs, separate accounts and explicit
    settlement observations. No inference of corporate-bond credit events,
    early exercise, uncovered short assignment or real broker cashflows.
    """
    from datetime import date
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from atlaspm.data.market_db import private_path
    from atlaspm.data.option_history import OptionHistory
    from atlaspm.data.treasury_history import TreasuryHistory
    from atlaspm.strategies.options.option_replay import replay_long_options
    from atlaspm.strategies.bonds.treasury_replay import replay_treasury_holding
    root=private_path(Path("local_data/runs"))
    root.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix="settlement-fixture-",dir=root) as folder:
        def write(name,body):
            path=Path(folder)/name
            path.write_text(body,encoding="utf-8")
            return path
        contracts=write("option_contracts.csv",
            "contract_id,underlying,right,strike,last_trade_at,expiration_at,multiplier,exercise_style,settlement_type,exercise_threshold_usd\n"
            "SYN-P,SYN-STOCK,put,100,2024-01-12T20:59Z,2024-01-12T21:00Z,100,american,physical,1\n"
            "SYN-C,SYN-INDEX,call,90,2024-01-12T20:59Z,2024-01-12T21:00Z,100,european,cash,1\n")
        quotes=write("option_quotes.csv","contract_id,quote_at,bid,ask,bid_size,ask_size\n"
            "SYN-P,2024-01-08T20:58Z,2,2.2,5,5\nSYN-C,2024-01-08T20:58Z,4,4.4,5,5\n")
        settlement=write("option_settlement.csv","contract_id,reference_at,available_at,cash_at,settlement_value\n"
            "SYN-P,2024-01-12T21:00Z,2024-01-12T21:02Z,2024-01-16T14:30Z,95\n"
            "SYN-C,2024-01-12T21:00Z,2024-01-12T21:02Z,2024-01-16T14:30Z,95\n")
        option=replay_long_options(OptionHistory(contracts,quotes,settlement),{
            "initial_cash_usd":2000,"initial_shares":{"SYN-STOCK":100},"fee_per_contract_usd":1,
            "orders":[{"at":"2024-01-08T21:00Z","contract_id":key,"side":"buy","contracts":1}
                      for key in ("SYN-P","SYN-C")],
            "valuation_times":["2024-01-12T21:01Z","2024-01-12T21:02Z","2024-01-16T14:30Z"]})
        terms=write("treasury_terms.csv","cusip,kind,dated_on,issued_on,maturity,annual_coupon_rate,face_increment_usd\n"
            "999999990,note,2023-12-31,2024-01-02,2024-12-31,0.04,100\n")
        payments=write("treasury_payments.csv","cusip,scheduled_on,paid_on\n"
            "999999990,2024-06-30,2024-07-01\n999999990,2024-12-31,2024-12-31\n")
        marks=write("treasury_quotes.csv","cusip,observed_on,clean_close_per_100\n"
            "999999990,2024-06-28,99.5\n999999990,2024-07-01,99.6\n999999990,2024-12-30,99.98\n")
        bond=replay_treasury_holding(TreasuryHistory(terms,payments,marks),"999999990",10000,
            [date.fromisoformat(x) for x in ("2024-06-28","2024-06-30","2024-07-01","2024-12-30","2024-12-31")],
            initial_cash_usd=100)
    waiting,due,paid=option["valuations"]
    first,coupon,receipt,_,maturity=bond["valuations"]
    checks={
        "option_no_future_settlement":waiting["option_bid_value_usd"] is None and waiting["cash_receivable_usd"]==0,
        "option_exercise_receivable":due["cash_receivable_usd"]==10500 and due["shares_available"]["SYN-STOCK"]==0,
        "option_cash_reconciliation":abs(paid["cash_available_usd"]-(2000-option["premium_paid_usd"]-
            option["execution_fees_usd"]+10500))<.01 and paid["cash_receivable_usd"]==0 and not paid["option_positions"],
        "bond_accrued_and_coupon":first["accrued_interest_usd"]>0 and coupon["payment_receivable_usd"]==200
            and receipt["payment_receivable_usd"]==0 and receipt["cash_available_usd"]==300,
        "bond_principal_reconciliation":maturity["held_face_usd"]==0 and maturity["cash_available_usd"]==10500
            and maturity["coupon_paid_to_date_usd"]==400 and maturity["principal_paid_to_date_usd"]==10000,
        "bond_nav_reconciliation":all(abs(v["nav_usd"]-v["cash_available_usd"]-v["payment_receivable_usd"]-
            v["dirty_market_value_usd"])<.01 for v in bond["valuations"]),
    }
    return {**FLAGS,"mode":"synthetic_settlement_fixture","checks":checks,
            "option_final_cash":paid["cash_available_usd"],"bond_final_cash":maturity["cash_available_usd"],
            "option_report_sha256":digest(option),"bond_report_sha256":digest(bond),
            "short_assignment_supported":False,"broker_side_effects":False}


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
