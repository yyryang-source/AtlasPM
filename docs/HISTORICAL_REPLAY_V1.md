# Historical Portfolio Replay V1 — synthetic core

Run from an isolated checkout after installing the package:
```powershell
& $py -m unittest discover -s tests -p "test_historical_replay*.py" -v
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

This is a standalone synthetic ledger prototype, now optionally integrated with the
unified optimizer, but not Paper OMS. No real historical data, margin borrowing,
corporate actions, benchmark comparison, model training or live routing.
A synthetic provenance claim is not proof of actual historical availability.
Real history requires independent security identity, corporate action, listing,
calendar and point-in-time validation, plus integration tests.

## V2 rolling allocation

Optional input: `"optimizer": {"method":"minimum_variance","lookback_sessions":60,"max_name_weight":0.6}`.
The seven methods from allocation.py are supported; CVaR requires at least
20 returns and risk budgeting requires explicit positive per-symbol budgets.
The replay uses a trailing window ending at the signal close, records
estimation dates and target weights, and waits for the next supplied open.
Warmup remains in cash. Held names must remain in the candidate universe.
Covariance uses annualized sample estimates plus 1e-8 diagonal regularization.
The targets are not guaranteed realized weights: opening gaps, integer shares,
costs and settlement can cause deviations. Constraints apply to optimizer
targets, not a continuous risk limit. No real history, margin or OMS integration.

## V3 comparison

Use `python -m atlaspm.engine.replay_performance --file examples/historical_replay_comparison.json`.
Requires the portfolio extra and a common optimizer configuration. All methods
use the same post-warmup dates, initial cash and cost assumptions.
Supply positive synthetic SPY and QQQ closing marks in each session's
benchmark_close and an explicit benchmark_basket. Benchmarks are frictionless
price-only buy-and-hold indices, not total-return series. The output includes
aligned equity indices, CAGR (252 supplied sessions/year), volatility, Sharpe,
Sortino, Calmar, drawdown, arithmetic alpha and beta versus SPY, tracking error,
information ratio, cumulative gross traded notional/start equity and costs.
Undefined ratios are null. Short synthetic samples are not investment evidence.
