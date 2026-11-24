# Historical Portfolio Replay V1 — synthetic core

Run from an isolated checkout after installing the package:
```powershell
& $py -m unittest discover -s tests -p "test_historical_replay.py" -v
& $py -m atlaspm.engine.replay --file examples/historical_replay_synthetic.json
```

Signal at session close, execution at the next supplied session open.
Integer shares, long-only, settled cash only; sale proceeds become available
after a configurable number of supplied sessions. Fees and adverse slippage
are charged on both sides. Every session reconciles overnight and intraday
P&L less costs to the change in equity. Missing held marks fail closed.
A final signal is reported as unexecuted rather than filled outside the sample.
Equal-weight top-K uses validated synthetic candidate scores with deterministic
tie-breaking. Cash-constrained purchases are processed alphabetically.
The supplied session sequence is not a validated exchange calendar.

This is a standalone synthetic ledger prototype, not yet integrated with the
unified optimizer or Paper OMS. No real historical data, margin borrowing,
corporate actions, benchmark comparison, model training or live routing.
A synthetic provenance claim is not proof of actual historical availability.
Real history requires independent security identity, corporate action, listing,
calendar and point-in-time validation, plus integration tests.
