"""Revalue a settled Treasury holding and reconcile coupon and principal receipts."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date
from math import exp, fsum, isfinite
from bisect import bisect_left
from typing import Any

from atlaspm.data.treasury_history import TreasuryHistory, TreasuryPayment


def _value_at_yield(annual_yield: float, cashflows: list[tuple[float, float]]) -> float:
    return fsum(amount * exp(-annual_yield * years) for years, amount in cashflows)


def curve_shock(cashflows, flat_rate, node_shocks_bps):
    """Illustrative key-rate shock on a flat continuously compounded base.

    Piecewise linear shocks in zero rates, flat extrapolation at endpoints.
    Positive key DV01 is a gain for a one bp downward bump, per 100 face.
    Not observed market curve, calibrated YTM or verified hedge sizing.
    """
    if (not isinstance(node_shocks_bps,dict) or not 2<=len(node_shocks_bps)<=12
            or any(type(k) not in (int,float) or not isfinite(k) or not 0<k<=50
                   or type(v) not in (int,float) or not isfinite(v) or abs(v)>10000
                   for k,v in node_shocks_bps.items())
            or not isfinite(flat_rate) or abs(flat_rate)>5
            or not cashflows or any(not isfinite(t) or not 0<t<=100 or not isfinite(a) or a<0
                                     for t,a in cashflows)):
        raise ValueError("INVALID_TREASURY_CURVE_SCENARIO")
    nodes=sorted(node_shocks_bps)
    def value(shocks):
        def bump(t):
            i=bisect_left(nodes,t)
            if i==0:
                return shocks[nodes[0]]
            if i==len(nodes):
                return shocks[nodes[-1]]
            lo,hi=nodes[i-1],nodes[i]
            return shocks[lo]+(shocks[hi]-shocks[lo])*(t-lo)/(hi-lo)
        return fsum(a*exp(-(flat_rate+bump(t)/10000)*t) for t,a in cashflows)
    zero=dict.fromkeys(nodes,0.)
    base=value(zero)
    if base<=0:
        raise ValueError("INVALID_TREASURY_CURVE_BASE")
    key={}
    for node in nodes:
        down={**zero,node:-1.}
        up={**zero,node:1.}
        key[str(node)]=(value(down)-value(up))/2
    return {"flat_base_value_per_100":base,"scenario_value_per_100":value(node_shocks_bps),
            "scenario_pnl_per_100":value(node_shocks_bps)-base,"key_rate_dv01_per_100":key,
            "parallel_dv01_per_100":(value(dict.fromkeys(nodes,-1.))-value(dict.fromkeys(nodes,1.)))/2,
            "model_only":True,"observed_curve_verified":False}


def _rate_risk(dirty: float, future: list[TreasuryPayment], at: date, shock_bps: float) -> dict[str, float]:
    cashflows = [((payment.paid_on - at).days / 365,
                  payment.coupon_per_100 + payment.principal_per_100) for payment in future]
    low, high = -5.0, 5.0
    if not _value_at_yield(high, cashflows) <= dirty <= _value_at_yield(low, cashflows):
        raise ValueError(f"Cannot fit a flat model rate to the Treasury dirty price on {at}")
    for _ in range(60):
        rate = (low + high) / 2
        if _value_at_yield(rate, cashflows) > dirty:
            low = rate
        else:
            high = rate
    rate = (low + high) / 2
    down = _value_at_yield(rate - 0.0001, cashflows)
    up = _value_at_yield(rate + 0.0001, cashflows)
    return {"model_flat_yield_continuous": rate, "dv01_per_100": (down - up) / 2,
            "shock_pnl_per_100": _value_at_yield(rate + shock_bps / 10000, cashflows) - dirty}


def replay_treasury_holding(history: TreasuryHistory, cusip: str, face_usd: int, dates: list[date], *,
                            initial_cash_usd: float = 0, max_quote_age_days: int = 3,
                            shock_bps: float = 100, include_opening_date_payment: bool = True,
                            curve_node_shocks_bps=None) -> dict[str, Any]:
    terms = history.terms[cusip]
    if (not dates or dates != sorted(set(dates)) or not terms.issued_on <= dates[0] < terms.maturity
            or type(face_usd) is not int or face_usd <= 0 or face_usd % terms.face_increment_usd
            or not isfinite(initial_cash_usd) or initial_cash_usd < 0
            or not isfinite(shock_bps) or abs(shock_bps) > 10000
            or type(max_quote_age_days) is not int or max_quote_age_days < 0):
        raise ValueError("Invalid Treasury holding face, initial cash, valuation dates, quote age or rate shock")

    schedule = history.payments[cusip]
    due_days = [payment.scheduled_on for payment in schedule]
    first_entitled = bisect_right(due_days, dates[0]) if not include_opening_date_payment else 0
    eligible = [payment for payment in schedule[first_entitled:] if dates[0] <= payment.scheduled_on <= dates[-1]]
    units = face_usd / 100
    cashflows = [{"scheduled_on": payment.scheduled_on.isoformat(), "paid_on": payment.paid_on.isoformat(),
                  "coupon_usd": units * payment.coupon_per_100, "principal_usd": units * payment.principal_per_100}
                 for payment in eligible]
    marks = []
    for day in dates:
        paid_coupon = units * fsum(event.coupon_per_100 for event in eligible if event.paid_on <= day)
        paid_principal = units * fsum(event.principal_per_100 for event in eligible if event.paid_on <= day)
        receivable = units * fsum(event.coupon_per_100 + event.principal_per_100 for event in eligible
                                  if event.scheduled_on <= day < event.paid_on)
        cash = initial_cash_usd + paid_coupon + paid_principal
        clean = None
        quote_on = None
        accrued = bond_value = dv01 = shock_pnl = 0.0
        rate = None
        curve_risk=None
        live_face = face_usd if day < terms.maturity else 0
        if live_face:
            quote = history.quote_at(cusip, day, max_quote_age_days)
            clean, quote_on = quote.clean_close_per_100, quote.observed_on.isoformat()
            index = bisect_right(due_days, day)
            start = due_days[index - 1] if index else terms.dated_on
            next_payment = schedule[index]
            accrued = next_payment.coupon_per_100 * (day - start).days / (next_payment.scheduled_on - start).days
            dirty = clean + accrued
            bond_value = units * dirty
            risk = _rate_risk(dirty, schedule[index:], day, shock_bps)
            rate = risk["model_flat_yield_continuous"]
            dv01, shock_pnl = units * risk["dv01_per_100"], units * risk["shock_pnl_per_100"]
            if curve_node_shocks_bps is not None:
                curve_risk=curve_shock([((p.paid_on-day).days/365,p.coupon_per_100+p.principal_per_100)
                                        for p in schedule[index:]],rate,curve_node_shocks_bps)
                curve_risk["scenario_pnl_usd"]=units*curve_risk["scenario_pnl_per_100"]
                curve_risk["key_rate_dv01_usd"]={k:units*v for k,v in curve_risk["key_rate_dv01_per_100"].items()}
        nav = cash + receivable + bond_value
        marks.append({"date": day.isoformat(), "quote_date": quote_on, "held_face_usd": live_face,
                      "clean_close_per_100": clean, "accrued_interest_usd": units * accrued,
                      "dirty_market_value_usd": bond_value, "cash_available_usd": cash,
                      "payment_receivable_usd": receivable, "coupon_paid_to_date_usd": paid_coupon,
                      "principal_paid_to_date_usd": paid_principal, "nav_usd": nav,
                      "model_flat_yield_continuous": rate, "dv01_usd": dv01,
                      "rate_shock_pnl_usd": shock_pnl, "rate_shock_nav_usd": nav + shock_pnl,
                      **({"curve_scenario":curve_risk} if curve_node_shocks_bps is not None else {})})
    return {"method": "nominal_treasury_settled_holding_replay", "cusip": cusip, "initial_face_usd": face_usd,
            "initial_cash_usd": initial_cash_usd, "rate_shock_bps": shock_bps, "max_quote_age_days": max_quote_age_days,
            "cashflows": cashflows, "valuations": marks, "live_execution_enabled": False}
