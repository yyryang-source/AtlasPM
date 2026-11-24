# AtlasPM roadmap (research-first)

This file describes **outstanding work**, not a promise of profitability or a release date.

## Architecture — end-to-end lifecycle

See [Standard Quant Workflow](QUANT_WORKFLOW.md) and run
`python -m atlaspm.framework --format json` to inspect the status of every
stage. This is a **contract and roadmap**, not a newly enabled trading bot.

Research steps: data governance → Alpha and factor research → sample-out
validation → asset-specific research → joint portfolio/risk/hedge design →
independent pretrade gate → synthetic execution/reconciliation →
monitoring/attribution → research feedback. Risk and compliance are
cross-cutting controls at every stage.

## Current — research stabilization

- Audit and reconcile verified corporate actions without rewriting raw quotes; add regression tests for repeated events and already-adjusted data.
- Repair the US equity point-in-time universe (IPO/delistings, identifier history, corporate actions, volume/liquidity, trusted fundamentals).
- Re-run all-market selection baselines on identical, cost-aware and *tie-safe* cohorts. Current V2 has not beaten the simple momentum ranker on expanded US data.
- Freeze research parameters before future holdout / paper tracking. Distinguish already-inspected history from independently held-out periods.

## Next — portfolio decision workbench

- Link verified candidate allowlist to a deterministic portfolio optimizer **in offline preview only**.
- Separate cash vs margin account ledgers, fee/cost estimates, concentration limits, stress tests and explicit no-trade state.
- Report rejected names, position sizing rationale, financing expense and scenario risk; do not force ten holdings when there are too few qualifying equities.

## Later — multi-asset strategy research

- Build separate options volatility/Greeks and bond curve/duration/credit
  research pipelines with honest contract-level data and cost models.
- Design a common risk exposure schema for equity beta, option Greeks,
  interest-rate duration, financing and tail protection.
- Jointly optimize hedges and portfolio construction in an offline preview.
  Nothing in the architecture registry generates live orders.

## Later — broker paper integration

- Add fully isolated Alpaca/IBKR paper-only adapters, explicit opt-in permissions, idempotent order intents, cancels/partial fills and reconciliation.
- Test failures, stale prices, disconnects and broker margin discrepancies before considering any live execution path.
- Develop a maintained UI once validated research and paper accounting outputs have stable schemas.

## Maintenance

`README.md` is the single current entry point. Topic-specific quantitative and selection methods stay in `docs/quant` and `docs/selection`; historical experiments remain in Git history or method notes. All new modules use canonical `atlaspm.quant` / `atlaspm.selection` paths. Legacy import shims will be removed only in a documented breaking change after tests and CLI consumers migrate.


## Autonomous Alpha research (stage 4, incremental)

See [Autonomous Alpha Engine](AUTONOMOUS_ALPHA_ENGINE.md) for implemented synthetic research, safety boundaries, CLI and remaining validation. Run `python -m atlaspm.cli autonomous-alpha --file local_data/alpha_request.json`. Production admission and live execution remain disabled.
