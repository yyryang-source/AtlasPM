"""Synthetic opening execution policy; not a broker or actual Paper OMS."""
from math import floor, sqrt
from atlaspm.engine.contracts import finite
from atlaspm.oms_controls import chain_audit_events, verify_audit_chain


def execute_order(symbol, side, requested, mark, cash_limit, previous_volume, policy, fee, slip, order_id, day):
    """Use previous completed session volume only, never current daily volume."""
    if type(requested) is not int or requested < 1 or side not in {"BUY","SELL"}:
        raise ValueError("INVALID_REPLAY_ORDER")
    participation = finite(policy.get("max_participation",.01),"max_participation",minimum=0)
    impact_bps = finite(policy.get("impact_bps_at_full_participation",50),"impact_bps",minimum=0)
    fraction = finite(policy.get("fill_fraction",1),"fill_fraction",minimum=0)
    if participation > 1 or fraction > 1 or impact_bps > 500:
        raise ValueError("INVALID_EXECUTION_POLICY")
    volume = finite(previous_volume,"previous_session_volume",minimum=0)
    capacity = floor(volume*participation)
    qty = min(requested,capacity,floor(requested*fraction),cash_limit)
    # Conservative impact based on maximum requested feasible participation,
    # not future realized liquidity. Price is fixed for this synthetic order.
    impact = impact_bps/10000*sqrt(min(requested,capacity)/volume) if volume else 0.
    adverse = slip+impact
    if adverse >= .1:
        raise ValueError("EXECUTION_COST_LIMIT")
    price = mark*(1+adverse if side=="BUY" else 1-adverse)
    events = [{"type":"SUBMIT"},{"type":"ACK"}]
    if qty:
        events.append({"type":"FILL","quantity":qty,"price":price,"fee":qty*price*fee})
    if qty < requested:
        events.extend([{"type":"CANCEL_REQUEST"},{"type":"CANCELED"}] if qty else [{"type":"REJECT"}])
    events = [dict(e,sequence=i+1,event_id=f"{order_id}:{i+1}",order_id=order_id,date=day)
              for i,e in enumerate(events)]
    audit,root = chain_audit_events(events)
    if not verify_audit_chain(audit,root):
        raise ValueError("REPLAY_ORDER_AUDIT_FAILED")
    cost = qty*abs(price-mark)+qty*price*fee
    return dict(order_id=order_id,date=day,symbol=symbol,side=side,requested=requested,
                filled=qty,unfilled=requested-qty,price=price,fee=qty*price*fee,
                cost=cost,status="FILLED" if qty==requested else "CANCELED" if qty else "REJECTED",
                impact_bps=impact*10000,prior_volume=volume,events=audit,audit_root_sha256=root)
