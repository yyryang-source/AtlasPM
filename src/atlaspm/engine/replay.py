"""Cash-only synthetic session replay. No broker or training permissions."""
from __future__ import annotations

import argparse
import json
from datetime import date
from math import floor, isclose
from pathlib import Path

from atlaspm.engine.contracts import finite, timestamp, validate_candidates


def replay(payload):
    if payload.get("mode") != "synthetic" or payload.get("synthetic_fixture") is not True:
        raise ValueError("REAL_HISTORY_NOT_APPROVED")
    cash = initial = finite(payload["initial_cash"], "initial_cash", minimum=0.01)
    fee = finite(payload.get("fee_bps", 5), "fee_bps", minimum=0) / 10000
    slip = finite(payload.get("slippage_bps", 5), "slippage_bps", minimum=0) / 10000
    reserve = finite(payload.get("cash_reserve", .1), "cash_reserve", minimum=0)
    top = payload.get("top_k", 2)
    cadence = payload.get("rebalance_sessions", 5)
    lag = payload.get("settlement_sessions", 1)
    if fee + slip >= .1 or reserve >= 1:
        raise ValueError("INVALID_COST_OR_RESERVE")
    for value in (top, cadence, lag):
        if type(value) is not int or value < 1:
            raise ValueError("INVALID_INTEGER_CONFIGURATION")
    sessions = payload["sessions"]
    if not isinstance(sessions, list) or len(sessions) < 2:
        raise ValueError("REQUIRES_TWO_SESSIONS")
    holdings, pending, curve, trades = {}, [], [], []
    targets = None
    history, allocations, orders = [], [], []
    policy = payload.get("execution_policy")
    if policy is not None:
        if not isinstance(policy, dict):
            raise ValueError("INVALID_EXECUTION_POLICY")
        from atlaspm.engine.replay_execution import execute_order
    prior_volume = {}
    def submit(s, side, qty, mark, budget, day):
        if s not in prior_volume:
            raise ValueError("MISSING_PRIOR_SESSION_VOLUME")
        order = execute_order(s, side, qty, mark, budget, prior_volume[s], policy,
                              fee, slip, f"SYN-{len(orders)+1}", str(day))
        orders.append(order)
        return order
    optimizer = payload.get("optimizer")
    if optimizer is not None:
        if not isinstance(optimizer, dict):
            raise ValueError("OPTIMIZER_CONFIG_MUST_BE_OBJECT")
        from atlaspm.engine.replay_allocation import rolling_target
        optimizer = dict(optimizer)
        if "min_cash_fraction" in optimizer and optimizer["min_cash_fraction"] != reserve:
            raise ValueError("CASH_RESERVE_CONFIGURATION_MISMATCH")
        optimizer["min_cash_fraction"] = reserve
    prior_day = prior_close = None
    prior_prices = {}
    prior_equity = peak = initial
    total_cost = 0.
    for i, row in enumerate(sessions):
        day = date.fromisoformat(row["date"])
        opening = timestamp(row["open_at"], "open_at")
        closing = timestamp(row["close_at"], "close_at")
        if not opening < closing or opening.date() != day or closing.date() != day:
            raise ValueError("INVALID_SESSION_TIMESTAMPS")
        if prior_day is not None and (day <= prior_day or opening <= prior_close):
            raise ValueError("SESSION_ORDER_OR_DUPLICATE")
        if row.get("source") != "synthetic":
            raise ValueError("UNVERIFIED_MARK_SOURCE")
        op = {s: finite(v, "open", minimum=.000001) for s,v in row["open"].items()}
        cl = {s: finite(v, "close", minimum=.000001) for s,v in row["close"].items()}
        if not op or set(op) != set(cl) or not set(holdings) <= set(op):
            raise ValueError("MISSING_MARK_OR_UNSUPPORTED_CORPORATE_ACTION")
        cash += sum(amount for due, amount in pending if due <= i)
        pending = [(due, amount) for due, amount in pending if due > i]
        opening_equity = cash + sum(v for _,v in pending) + sum(q*op[s] for s,q in holdings.items())
        cost_today = 0.
        if targets is not None:
            signal_day, signal_close, weights = targets
            if not signal_close < opening or not set(weights) <= set(op):
                raise ValueError("TARGET_NOT_AVAILABLE_OR_MISSING_EXECUTION_MARK")
            desired = {s: floor(opening_equity*w/op[s]) for s,w in weights.items()}
            for s in sorted(set(holdings) | set(desired)):
                qty = max(0, holdings.get(s,0)-desired.get(s,0))
                if qty:
                    if policy is not None:
                        order = submit(s, "SELL", qty, op[s], 0, day)
                        qty = order["filled"]
                        if not qty:
                            continue
                        cost = order["cost"]
                    else:
                        cost = qty*op[s]*(slip + (1-slip)*fee)
                    notional = qty*op[s]
                    pending.append((i+lag, notional-cost))
                    holdings[s] -= qty
                    cost_today += cost
                    trades.append(dict(date=str(day), signal_date=signal_day, symbol=s, side="SELL", shares=qty, reference_price=op[s], cost=cost))
            for s in sorted(desired):
                unit = op[s]*(1+slip)*(1+fee)
                qty = min(max(0, desired[s]-holdings.get(s,0)), max(0, floor((cash-opening_equity*reserve)/unit)))
                if policy is not None:
                    requested = max(0, desired[s]-holdings.get(s,0))
                    if requested:
                        order = submit(s, "BUY", requested, op[s], max(0,cash-opening_equity*reserve), day)
                        qty = order["filled"]
                        unit = order["price"]*(1+fee)
                if qty:
                    cash -= qty*unit
                    holdings[s] = holdings.get(s,0)+qty
                    cost = qty*(unit-op[s])
                    cost_today += cost
                    trades.append(dict(date=str(day), signal_date=signal_day, symbol=s, side="BUY", shares=qty, reference_price=op[s], cost=cost))
            holdings = {s:q for s,q in holdings.items() if q}
            after = cash+sum(v for _,v in pending)+sum(q*op[s] for s,q in holdings.items())
            if cash < -1e-8 or not isclose(after, opening_equity-cost_today, abs_tol=1e-7):
                raise ValueError("LEDGER_RECONCILIATION_FAILED")
        targets = None
        overnight = sum(q*(op[s]-prior_prices[s]) for s,q in prior_holdings.items()) if i else 0.
        intraday = sum(q*(cl[s]-op[s]) for s,q in holdings.items())
        equity = cash+sum(v for _,v in pending)+sum(q*cl[s] for s,q in holdings.items())
        if not isclose(equity-prior_equity, overnight+intraday-cost_today, abs_tol=1e-7):
            raise ValueError("DAILY_ATTRIBUTION_FAILED")
        peak = max(peak,equity)
        total_cost += cost_today
        curve.append(dict(date=str(day), equity=equity, settled_cash=cash,
                          unsettled_cash=sum(v for _,v in pending), holdings=dict(holdings),
                          daily_return=equity/prior_equity-1, drawdown=equity/peak-1,
                          overnight_pnl=overnight, intraday_pnl=intraday, trade_cost=cost_today))
        if policy is not None:
            raw_volume = row.get("volume")
            if not isinstance(raw_volume, dict) or set(raw_volume) != set(cl):
                raise ValueError("MISSING_COMPLETED_SESSION_VOLUME")
            prior_volume = {s:finite(v,"volume",minimum=0) for s,v in raw_volume.items()}
        history.append((str(day), cl))
        if i % cadence == 0:
            candidates = validate_candidates(row["signals"], as_of=closing, max_age_seconds=86400)
            if not all(c.symbol in cl for c in candidates):
                raise ValueError("SIGNAL_MARK_MISSING")
            chosen = sorted(candidates,key=lambda c:(-c.score,c.symbol))[:top]
            if optimizer is None:
                targets = (str(day), closing, {c.symbol:(1-reserve)/len(chosen) for c in chosen})
            else:
                # Retain held names in the optimization universe; no silent discard.
                selected = [c for c in candidates if c in chosen or c.symbol in holdings]
                result = rolling_target(selected, history, holdings, cash,
                                        sum(v for _,v in pending), optimizer)
                if result is not None:
                    allocations.append(dict(signal_date=str(day), **result))
                    targets = (str(day), closing, result["weights"])
        prior_day, prior_close, prior_prices, prior_equity = day, closing, cl, equity
        prior_holdings = dict(holdings)
    return dict(research_only=True, live_execution_enabled=False,
                approved_for_model_training=False, historical_security_identity_verified=False,
                broker_order_submission_enabled=False, execution_model="synthetic_next_open_cash_only",
                curve=curve, trades=trades, allocations=allocations, orders=orders, total_cost=total_cost,
                total_return=prior_equity/initial-1, max_drawdown=min(p["drawdown"] for p in curve),
                unexecuted_final_signal=targets is not None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(json.loads(args.file.read_text(encoding="utf-8"))), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
