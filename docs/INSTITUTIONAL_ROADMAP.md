# AtlasPM Institutional Product Roadmap

This roadmap **replaces feature-by-feature backtest tinkering as the primary direction**. Each stage has a verifiable definition of done (DoD). The existing 2000–2026 research notebooks, risk tests, SPY benchmark and v0.3.x historical code remain useful but do not count as integrated institutional infrastructure.

## Phase A — Portfolio domain and policy contracts (v0.4)

- [ ] Typed stock/ETF, UST CUSIP (bills, notes, bonds), listed option, cash/debt and FX instruments.
- [x] Initial USD read-only account ledger and synthetic instruments: stock/ETF, Treasury dirty valuation and signed listed option marks
- [x] Independent cash and margin accounts, debt/financing-interest accounting, broker NLV reconciliation flags and quote freshness checks
- [x] Treasury broker-recognized informational collateral tally without double counting NLV or available cash
- [ ] Event-sourced balance/position ledger and settlement/coupon/assignment state transitions
- [ ] Cash, margin, option and collateral ledger with cash/position/debt invariant, coupon, accrued interest, options expiry/assignment.
- [ ] Central pure-function risk gate: no new exposure unless broker snapshot, order preview, eligibility and data freshness are valid.
- [ ] Policy-driven risk states: NORMAL/WATCH/DE_RISK/CRITICAL/HALT with hysteresis and alert reasons.
- [ ] Synthetic-only CI data and tests for unknown collateral, short options, stale quotes and breached margin.
- DoD: fail-closed checks pass offline; no real accounts required; audit decisions deterministic.

## Phase B — Multi-asset optimizer and trade construction (v0.5)

- [ ] Separate cash and financing account allocation with capital transfer limits.
- [ ] Stock/ETF/Treasury/cash weights, target diversification, sector/beta limits and turnover/fees.
- [ ] Position add/reduce/hold decision policy with hysteresis, rebalance bands, minimum holding/round lot support.
- [x] Initial **read-only single-equity BUY/SELL planner** connected to the reconciled ledger and central risk gate; projects share count, cash, borrowing and costs
- [x] Require order-bound mock broker preview and reject misaligned account/side/ticker/quantity/limit price/debt
- [x] Reserve unsettled liabilities from projected available settled cash and fail closed on missing preview or stale marks
- [x] Research-only coordinated single-account equity, existing Treasury, long put and margin repayment basket; matched synthetic combined preview and separate per-leg RMS vetoes
- [x] Block use of unsettled equity-sale proceeds for immediate purchases, prevent new Treasury collateral double counting and check covered puts
- [x] First offline event-replayed paper OMS prototype with 5 order states, terminal status transitions, partial fills and synthetic settlement
- [x] Idempotent duplicate execution/event handling and UNKNOWN_OUTCOME account reconciliation halt
- [x] Cash/debt/stock/Treasury/long-option post-fill reconciliation and synthetic audit journal
- [ ] Persist event sourcing in an append-only database and re-risk partial fills with genuine broker data
- [ ] Authenticated per-leg broker What-If previews and multi-asset partial-fill/order-state orchestration
- [ ] Full target-weight rebalancing, multi-leg collateral/option handling, broker order previews and event-sourced settled cash accounting
- [ ] Pre- and post-trade pro forma account simulation, margin buffer and collateral encumbrance.
- [ ] Risk budgets: VaR, ES, drawdown, liquidity, correlated-equity and yield curve stress, financing sensitivity.
- DoD: reproducible simulated rebalance and audit trail; risk gate rejects pro forma breaches; holdings/fees/debt reconcile.

## Phase C — Treasury collateral & funding engine (v0.6)

- [ ] CUSIP master, clean/dirty price, coupon/accrued interest, maturity, duration/DV01 and Treasury roll.
- [ ] Distinguish Treasury purchase, external transfer and repo facility. Broker-recognized eligible collateral from account snapshot ONLY.
- [ ] Brokerage security-specific margin and haircuts, settled cash, loan interest tiers, margin utilization and liquidity reserve.
- [ ] Scenario analysis: equity selloff + Treasury yield jump + haircut expansion + margin increase.
- DoD: no double counting; missing collateral eligibility blocks adds; reconcile broker What-If preview.

## Phase D — Options overlay / hedge engine (v0.7)

- [ ] Listed option contract identity, multiplier, expiry, strike, exercise style and settlement.
- [ ] Option chains with timestamped bid/ask/IV; Greeks and scenario repricing; expiry/assignment.
- [ ] Protective put, put spread, collar, ETF index hedge; premium budget and hedge coverage.
- [ ] Expected protection by market shock, theta carry, beta basis, gamma/vega and short-call assignment.
- DoD: option payoff and fee tests pass; stale chains or missing permissions block live-capable hedge construction.

## Phase E — Broker adapter + safe order management (v0.8)

- [ ] IBKR read-only account sync first, then official order-preview/What-If.
- [ ] Paper account only: target rebalancing, cancel/replace, partial fills, duplicate protection, reconnection recovery.
- [ ] Signed approval/event audit, kill switch, stale data and order ambiguity fail-closed modes.
- [ ] Explicit separate execution privileges, broker account allow-list, low-risk limit orders; live disabled.
- DoD: simulated reconnection, duplicate, partial fill, rejected order, margin change and kill-switch tests pass.

## Phase F — Research ML, model registry, explainability (v0.9)

- [ ] Point-in-time fundamentals, macro rates, daily/option-chain data provenance.
- [ ] Train/predict workflows on Windows PowerShell, model cards, dataset versions, confidence and drift monitoring.
- [ ] Multi-regime out-of-sample evaluation, walk-forward parameter selection, multiple-testing guardrails.
- [ ] AI proposes and explains trade candidates; central deterministic RMS retains absolute veto.
- DoD: repeatable local training, benchmarked out-of-sample against SPY/risk-matched portfolios; no unverified alpha claims.

## Phase G — Professional dashboard and security review (v1.0)

- [ ] Account-level and consolidated exposures and portfolio attribution.
- [ ] Greeks, maturity buckets, Treasury pledged collateral, buying power, risk state and daily event history.
- [ ] Policy controls and human approval UI, charts and reproducible reports.
- [ ] Security hardening, least privilege, user data privacy and documented compliance/legal review.
- DoD: paper system passes end-to-end acceptance tests. Live launch is a **separate gated decision**, not automatic.

## Immediate implementation order

1. [x] Publish architecture contracts, risk policy and synthetic sample; preserve previous strategy/test behavior.
2. [x] Build pure read-only risk gate with broker snapshot validation and block/allow rationale.
3. [partial] Build separate multi-asset portfolio ledger; coupon cashflow event engine remains pending.
4. Build add/reduce/hedge proposal engine and paper OMS.
5. Add options pricing/scenario/payoff model and Treasury collateral with actual broker preview.
6. Build dashboard and more realistic out-of-sample tests before any live trading.

## Non-negotiable limits

A fund or bank-scale platform requires market data licenses, operational monitoring, back-office controls, regulatory assessment, and extensive independent testing. Calling this project "institutional-grade" is a target standard, not a currently verified property. Public GitHub must never contain customer records, account IDs, brokerage authentication, order-routing secrets, or proprietary licensed market data.
