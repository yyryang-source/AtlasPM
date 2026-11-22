# AtlasPM v0.4.5 — After-Fill Risk Controls and Audit Hash Chain

**Scope:** Offline replay of fictional order events only. No IBKR or other broker is connected. No actual paper or live trades occur.

The previous Paper OMS checked the planned basket before any fill and reconciled marked assets, cash and debt after each event. v0.4.5 adds two protections:

1. After each **unique FILL**, recalculate the replayed gross marked exposure and conservative equity/Treasury shock loss relative to current marked equity. Compare them to the original plan's maximum gross leverage and stress-loss policy. Do not credit the long put as a guarantee of stress protection. Negative cash, illegal borrowing and nonpositive equity also trigger checks.
2. Build a deterministic **SHA-256 hash chain** over each unique audit entry, including the original source event, transition, account equity and reconciliation gap. Identical replays yield the same root hash.

## Run on Windows PowerShell

~~~powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_oms_controls.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli oms-replay --ledger examples\institutional_batch_ledger.json --plan examples\institutional_batch_plan.json --events examples\paper_oms_events.json --output local_data\paper_oms_report.json
~~~

The same 13-event synthetic replay should still finish with PAPER_REPLAY_COMPLETED, four FILLED orders, five unique fills checked, $123,945 marked net equity, $48,000 debt, a zero reconciliation gap and no post-fill static risk breach.

## Mid-basket exposure risk

An approved four-leg proposal may meet policy **after all intended legs finish**, but intermediate fills can violate policy. For instance, buying a Treasury before reducing QQQ can temporarily raise marked gross exposure above a 1.34x risk cap even when the final planned basket is about 1.333x.

On this intermediate breach the simulator records POST_FILL_GROSS_LEVERAGE_EXCEEDS_BUDGET, flags HALT_RISK if that is the final recorded journal event, and refuses subsequent unique events until separate reconciliation. This is a **replay halt**, not an order cancellation or a reversal of a real fill.

A real system would still have to query the broker, cancel remaining exposure-increasing orders, reconcile ambiguous order outcomes and request operator approval for any corrective action.

## JSON and CLI fields

- post_fill_risk_snapshots: per-unique-FILL gross-to-equity, stress loss, liquid cash after payables, ignored put market value, breach codes and a false broker recheck flag
- post_fill_risk_breaches: all static-limit breaches observed in replay
- audit_events: ordered state transitions, original source event and chained previous_hash / event_hash
- audit_root_sha256: root hash of the local audit log
- audit_hash_chain_self_consistent: replay-verified chain internal consistency
- broker_margin_rechecked_after_fills: false; the actual broker was NOT queried

**Security limitation:** SHA-256 chained entries can indicate tampering *only if a trusted independent root was saved*. An attacker able to rewrite both events and root can forge a new internally consistent chain. This is NOT digitally signed, append-only external storage, trustworthy time stamping or proof of broker-authenticated fills.

**Risk limitation:** Static gross and equity/Treasury shock calculations are not actual broker maintenance, portfolio margin, changing house requirements, a full options Greek risk model, realistic execution, market gaps or verified T+1 business settlement. No live connectivity, order routing or real accounts exist. Additional independent read-only brokerage reconciliation and controls are necessary before any live system is considered.

Refer to PAPER_OMS.md for the original event replay semantics.
