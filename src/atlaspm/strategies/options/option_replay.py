"""Replay long option positions against historical quotes and explicit deliveries."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Any

from atlaspm.data.option_history import OptionHistory, option_timestamp


def _amount(raw: object, label: str) -> float:
    if type(raw) not in {int, float} or not isfinite(raw) or raw < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return float(raw)


def _quantity(raw: object, label: str) -> int:
    if type(raw) is not int or raw <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return raw


@dataclass(frozen=True)
class OptionOrder:
    at: datetime
    contract_id: str
    side: str
    contracts: int
    cash_at: datetime | None


@dataclass(frozen=True)
class Delivery:
    contract_id: str
    cash_usd: float = 0.0
    stock: str | None = None
    shares: int = 0


def expiry_delivery(key, contract, settlement, count, *, cash, underlying_shares):
    """Canonical long-option expiry payoff; used by replay and the paper OMS.

    No short assignment, early exercise or unconfirmed settlement is inferred.
    Cash debit and share debit occur at exercise; delivery is received later.
    """
    count=_quantity(count,"held contracts")
    spread=settlement.settlement_value-contract.strike
    intrinsic=max(0.,spread if contract.right=="call" else -spread)*contract.multiplier
    exercised=intrinsic>0 and intrinsic+1e-9>=contract.exercise_threshold_usd
    cash_debit=0.; shares_debit=0; delivery=None
    if exercised:
        units=count*contract.multiplier
        if contract.settlement_type=="cash":
            delivery=Delivery(key,cash_usd=count*intrinsic)
        elif contract.right=="put":
            if underlying_shares<units:
                raise ValueError(f"Insufficient stock for physical put delivery: {key}")
            shares_debit=units
            delivery=Delivery(key,cash_usd=units*contract.strike)
        else:
            cash_debit=units*contract.strike
            if cash_debit>cash:
                raise ValueError(f"Insufficient cash for physical call exercise: {key}")
            delivery=Delivery(key,stock=contract.underlying,shares=units)
    return intrinsic,exercised,cash_debit,shares_debit,delivery


def replay_long_options(history: OptionHistory, plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict) or not isinstance(plan.get("orders"), list):
        raise ValueError("Option replay plan needs an orders list")
    if not isinstance(plan.get("valuation_times"), list) or not plan["valuation_times"]:
        raise ValueError("Option replay plan needs valuation_times")
    initial_cash = _amount(plan.get("initial_cash_usd"), "initial_cash_usd")
    cash = initial_cash
    raw_shares = plan.get("initial_shares", {})
    if not isinstance(raw_shares, dict):
        raise ValueError("initial_shares must be an object")
    shares: Counter[str] = Counter()
    for name, number in raw_shares.items():
        symbol = name.strip().upper()
        if not symbol or symbol in shares:
            raise ValueError("Empty or duplicate initial stock symbol")
        shares[symbol] = _quantity(number, "initial shares")
    fee_per_contract = _amount(plan.get("fee_per_contract_usd", 0), "fee_per_contract_usd")
    age = _quantity(plan.get("max_quote_age_minutes", 5), "max_quote_age_minutes")
    max_age = timedelta(minutes=age)
    valuations = {option_timestamp(text) for text in plan["valuation_times"]}
    end = max(valuations)

    orders: dict[datetime, list[OptionOrder]] = defaultdict(list)
    maturity: dict[datetime, list[str]] = defaultdict(list)
    expirations: dict[datetime, list[str]] = defaultdict(list)
    timeline = set(valuations)
    order_keys = set()
    traded_contracts = set()
    for row in plan["orders"]:
        if not isinstance(row, dict):
            raise ValueError("Option orders must be objects")
        key = row["contract_id"]
        contract = history.contracts[key]
        side = row["side"]
        at = option_timestamp(row["at"])
        proceeds_at = option_timestamp(row["cash_at"]) if row.get("cash_at") is not None else None
        if (side not in {"buy", "sell"} or at > min(contract.last_trade_at, end) or at >= contract.expiration_at
                or (side == "sell" and (proceeds_at is None or proceeds_at <= at))
                or (side == "buy" and proceeds_at is not None)):
            raise ValueError(f"Invalid planned option order chronology or side for {key}")
        if (at, key) in order_keys:
            raise ValueError(f"Duplicate option order for {key} at {at.isoformat()}")
        order_keys.add((at, key))
        orders[at].append(OptionOrder(at, key, side, _quantity(row["contracts"], "contracts"), proceeds_at))
        timeline.add(at)
        if proceeds_at is not None:
            timeline.add(proceeds_at)
        traded_contracts.add(key)

    for key in sorted(traded_contracts):
        expiration = history.contracts[key].expiration_at
        expirations[expiration].append(key)
        timeline.add(expiration)
        settlement = history.settlements.get(key)
        if settlement is not None:
            effective = max(expiration, settlement.available_at)
            maturity[effective].append(key)
            timeline.update((effective, settlement.cash_at))

    positions: Counter[str] = Counter()
    consumed: Counter[tuple[str, datetime, str]] = Counter()
    deliveries: dict[datetime, list[Delivery]] = defaultdict(list)
    fills = []
    exercises = []
    cash_movements = []
    snapshots = []
    premium_paid = premium_received = transaction_fees = spread_cost = 0.0

    for at in sorted(timestamp for timestamp in timeline if timestamp <= end):
        for item in deliveries.pop(at, []):
            cash += item.cash_usd
            if item.stock is not None:
                shares[item.stock] += item.shares
            cash_movements.append({"at": at.isoformat(), "contract_id": item.contract_id,
                                   "cash_usd": item.cash_usd, "stock": item.stock, "shares": item.shares})

        for key in expirations.get(at, []):
            if positions[key] and key not in history.settlements:
                raise ValueError(f"Missing official expiration settlement for held contract {key}")
        for key in maturity.get(at, []):
            count = positions.pop(key, 0)
            if not count:
                continue
            contract = history.contracts[key]
            settlement = history.settlements[key]
            intrinsic_usd,exercised,cash_debit,shares_debit,delivery=expiry_delivery(
                key,contract,settlement,count,cash=cash,underlying_shares=shares[contract.underlying])
            cash-=cash_debit
            if shares_debit:
                shares[contract.underlying]-=shares_debit
            if delivery is not None:
                deliveries[settlement.cash_at].append(delivery)
            exercises.append({"at": at.isoformat(), "contract_id": key, "contracts": count,
                              "exercised": exercised, "settlement_type": contract.settlement_type,
                              "reference_value": settlement.settlement_value, "intrinsic_usd_per_contract": intrinsic_usd,
                              "cash_or_delivery_at": settlement.cash_at.isoformat() if exercised else None})

        for order in orders.get(at, []):
            key = order.contract_id
            contract = history.contracts[key]
            quote = history.quote_at(key, at, max_age)
            buy = order.side == "buy"
            price, size = (quote.ask, quote.ask_size) if buy else (quote.bid, quote.bid_size)
            quote_key = (key, quote.at, order.side)
            if price <= 0 or consumed[quote_key] + order.contracts > size:
                raise ValueError(f"Insufficient displayed option liquidity: {key}")
            if not buy and order.contracts > positions[key]:
                raise ValueError(f"Option selling requires an existing long position: {key}")
            premium = price * contract.multiplier * order.contracts
            fee = fee_per_contract * order.contracts
            spread_charge = (quote.ask - quote.bid) * .5 * contract.multiplier * order.contracts
            if buy:
                if premium + fee > cash:
                    raise ValueError(f"Insufficient cash for option purchase: {key}")
                cash -= premium + fee
                positions[key] += order.contracts
                premium_paid += premium
            else:
                if fee > cash:
                    raise ValueError(f"Insufficient cash for option sale fee: {key}")
                cash -= fee
                positions[key] -= order.contracts
                deliveries[order.cash_at].append(Delivery(key, cash_usd=premium))
                premium_received += premium
            consumed[quote_key] += order.contracts
            transaction_fees += fee
            spread_cost += spread_charge
            fills.append({"at": at.isoformat(), "quote_at": quote.at.isoformat(), "contract_id": key,
                          "contracts": order.contracts, "side": order.side, "fill_price": price,
                          "premium_usd": premium, "fee_usd": fee, "spread_cost_vs_mid_usd": spread_charge})

        if at in valuations:
            market_value = 0.0
            awaiting = []
            active = {key: count for key, count in positions.items() if count > 0}
            for key, count in active.items():
                contract = history.contracts[key]
                if at > contract.last_trade_at or at >= contract.expiration_at:
                    awaiting.append(key)
                else:
                    bid = history.quote_at(key, at, max_age).bid
                    market_value += bid * count * contract.multiplier
            pending_cash = sum(item.cash_usd for items in deliveries.values() for item in items)
            pending_shares = Counter()
            for items in deliveries.values():
                for item in items:
                    if item.stock is not None:
                        pending_shares[item.stock] += item.shares
            snapshots.append({"at": at.isoformat(), "cash_available_usd": cash, "cash_receivable_usd": pending_cash,
                              "shares_available": dict(shares), "shares_receivable": dict(pending_shares),
                              "option_positions": active, "awaiting_reference": awaiting,
                              "option_bid_value_usd": None if awaiting else market_value})

    return {
        "method": "historical_long_option_replay",
        "initial_cash_usd": initial_cash,
        "premium_paid_usd": premium_paid, "premium_sold_usd": premium_received,
        "execution_fees_usd": transaction_fees, "spread_cost_vs_mid_usd": spread_cost,
        "fills": fills, "expirations": exercises, "deliveries": cash_movements, "valuations": snapshots,
        "live_execution_enabled": False,
    }
