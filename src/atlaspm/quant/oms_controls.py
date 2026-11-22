"""Deterministic post-fill limits and unsiged SHA-256 paper audit-chain helpers.

These are LOCAL diagnostics only; hashes are not digital signatures and do
not establish that a simulated event came from a real broker.
"""

from __future__ import annotations

from hashlib import sha256
from json import dumps
from math import isfinite
from typing import Any, Mapping, Sequence


def canonical_bytes(value: Any) -> bytes:
    """Reject NaN and non-JSON values before producing stable audit bytes."""
    return dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def chain_audit_events(entries: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Link event, timestamp, result and supplied source event into a hash chain."""
    previous_hash = "0" * 64
    records = []
    for original in entries:
        entry = dict(original)
        if any(k in entry for k in ("previous_hash", "event_hash")):
            raise ValueError("Audit entry already has a hash chain")
        digest = sha256(bytes.fromhex(previous_hash) + canonical_bytes(entry)).hexdigest()
        records.append({
            **entry, "previous_hash": previous_hash, "event_hash": digest,
        })
        previous_hash = digest
    return records, previous_hash


def verify_audit_chain(entries: Sequence[Mapping[str, Any]], expected_root: str) -> bool:
    """Verify a self-consistent local chain. Does NOT authenticate its author."""
    if not isinstance(expected_root, str) or len(expected_root) != 64:
        return False
    previous = "0" * 64
    for item in entries:
        if not isinstance(item, dict):
            return False
        record = {k: v for k, v in item.items() if k not in ("event_hash", "previous_hash")}
        if item.get("previous_hash") != previous:
            return False
        try:
            calc = sha256(bytes.fromhex(previous) + canonical_bytes(record)).hexdigest()
        except (ValueError, TypeError, OverflowError):
            return False
        if item.get("event_hash") != calc:
            return False
        previous = calc
    return previous == expected_root


def post_fill_risk_snapshot(
    positions: Sequence[Mapping[str, Any]],
    *,
    marked_net_liquidation: float,
    settled_cash: float,
    unsettled_payable: float,
    margin_debt: float,
    account_type: str,
    max_gross_leverage: float,
    max_stress_loss_fraction: float,
    equity_decline_fraction: float,
    treasury_decline_fraction: float,
) -> dict[str, Any]:
    """Conservative marked-risk limit evaluation after an invented paper fill.

    Long put mark NOT credited as protection. No broker margin is simulated.
    """
    for field, value in (
        ("marked_net_liquidation", marked_net_liquidation),
        ("settled_cash", settled_cash),
        ("unsettled_payable", unsettled_payable),
        ("margin_debt", margin_debt),
        ("max_gross_leverage", max_gross_leverage),
        ("max_stress_loss_fraction", max_stress_loss_fraction),
        ("equity_decline_fraction", equity_decline_fraction),
        ("treasury_decline_fraction", treasury_decline_fraction),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"{field} must be finite")
    if max_gross_leverage <= 0 or max_stress_loss_fraction < 0:
        raise ValueError("Limits must be nonnegative; leverage cap must be positive")
    if not (0 <= equity_decline_fraction <= 1 and 0 <= treasury_decline_fraction <= 1):
        raise ValueError("Shock fractions must be in [0, 1]")
    if account_type not in {"cash", "margin"}:
        raise ValueError("account_type must be cash or margin")
    equity_mv = sum(p["market_value"] for p in positions if p["type"] == "equity")
    treasury_mv = sum(p["market_value"] for p in positions if p["type"] == "treasury")
    gross_mv = sum(p["gross_notional"] for p in positions)
    long_put_premium_value = sum(
        p["market_value"] for p in positions
        if p["type"] == "option" and p["right"] == "put" and p["contracts"] > 0
    )
    blockers: list[str] = []
    if marked_net_liquidation <= 0:
        blockers.append("POST_FILL_NONPOSITIVE_EQUITY")
    if account_type == "cash" and margin_debt > 1e-8:
        blockers.append("POST_FILL_CASH_ACCOUNT_BORROWING")
    if settled_cash < -1e-8:
        blockers.append("POST_FILL_NEGATIVE_SETTLED_CASH")
    if margin_debt < -1e-8 or unsettled_payable < -1e-8:
        blockers.append("POST_FILL_NEGATIVE_LIABILITY")
    denominator = max(marked_net_liquidation, 1e-8)
    gross = gross_mv / denominator
    loss = (equity_mv * equity_decline_fraction + treasury_mv * treasury_decline_fraction) / denominator
    if gross > max_gross_leverage + 1e-8:
        blockers.append("POST_FILL_GROSS_LEVERAGE_EXCEEDS_BUDGET")
    if loss > max_stress_loss_fraction + 1e-8:
        blockers.append("POST_FILL_STRESS_LOSS_EXCEEDS_BUDGET")
    return {
        "marked_net_liquidation": marked_net_liquidation,
        "gross_marked_notional": gross_mv,
        "gross_to_net_liquidation": gross,
        "conservative_stress_loss_fraction": loss,
        "long_put_mark_value_not_credited_to_stress": long_put_premium_value,
        "available_settled_cash_after_existing_payables": max(0.0, settled_cash - unsettled_payable),
        "post_fill_risk_state": "HALT_RISK" if blockers else "WITHIN_STATIC_LIMITS",
        "breaches": blockers,
        "broker_margin_or_whatif_revalidated": False,
    }
