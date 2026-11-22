# AtlasPM roadmap

## v0.1 — foundation (implemented)

- [x] Python package and Windows PowerShell quick start
- [x] Long-only cash and margin account accounting
- [x] Illustrative maintenance-margin and decline stress tests
- [x] Borrowing-cost estimate
- [x] Capped inverse-volatility allocation baseline
- [x] Automated unit tests and CI

## v0.2 — constrained optimization and historical research (in progress)

- [x] Deterministic long-only mean-variance prototype with covariance validation and concentration caps
- [x] User-defined leverage and financing assumptions in scenario output
- [x] Make cash-account maintenance requirement zero
- [ ] Connect historical data and estimate returns/covariance with provenance

### Remaining data work

- [ ] Data import (CSV first; optional vendors later), provenance and calendar alignment
- [ ] Total return and corporate-action handling
- [ ] Walk-forward backtesting, look-ahead/leakage checks, realistic fees and slippage
- [ ] Historical multi-asset covariance, factor and drawdown risk reports

## v0.3 — optimization and AI experiments

- [ ] CVaR optimizer and turnover-aware, broker-compatible risk constraints
- [ ] Out-of-sample baselines before training
- [ ] Local ML training on Windows with reproducible seeds/configs
- [ ] Leakage-safe validation and model cards; no unsupported return promises

## v0.4 — broker sandbox

- [ ] Read-only positions and balances adapter
- [ ] Broker-supported simulated/paper order routing
- [ ] Reconciliation, audit logs, rate limits, idempotency
- [ ] Live trading permanently off by default; explicit permissions and safety review required

## v0.5 — interface

- [ ] Browser dashboard for balances, portfolios and risk explanations
- [ ] Scenario comparison, cash/margin account selection and approval workflow

Priority: make the accounting auditable before adding forecasting or automated execution.
