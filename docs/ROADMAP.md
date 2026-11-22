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
- [x] Download adjusted Yahoo Finance daily prices via optional yfinance; estimate historical annualized returns/covariance with provenance

### Remaining data work

- [x] Strict CSV schema, common-date calendar alignment and source metadata
- [ ] Additional vetted data providers and corporate-action validation
- [ ] Total return and corporate-action handling
- [x] Walk-forward rolling backtest with no future-return exposure, fee and financing assumptions, drawdowns and same-universe benchmark
- [ ] Improve slippage, exchange-calendar handling, broker-specific margin requirements and forced-liquidation modeling
- [ ] Historical multi-asset covariance, factor and drawdown risk reports

## v0.2.1 — backtest prototype (implemented)

- [x] 3-year rolling lookback and quarterly rebalance defaults
- [x] Buy-and-hold equal-weight benchmark on matching dates
- [x] Position drift, gross-leverage resets, daily financing interest and trading costs
- [x] Research-only maintenance-breach flags (not liquidation)

## v0.2.2 — risk-audit prototype (implemented)

- [x] Match portfolio universes, parameter settings and daily return dates
- [x] Report leveraged maintenance-breach dates, calendar-year returns and benchmark comparisons
- [x] Flag model risk and refuse claims of live execution suitability

## v0.2.3 — independent benchmark and margin stress mode (implemented, awaiting local validation)

- [x] Optional delayed, full deleveraging response to hypothetical maintenance breaches
- [x] Preserve original non-liquidating scenario by default
- [x] SPY buy-and-hold benchmark downloaded independently and aligned without forward fill
- [x] Offline regression tests for SPY alignment and forced-deleveraging state transitions
- [ ] Validate 2003–2026 strategy impact with real user-downloaded SPY data and margin stress outputs

## v0.2.4 — historical relative-risk diagnostics (implemented; awaiting local validation)

- [x] SPY-relative Beta, correlation, tracking error and information ratio
- [x] Calendar-year compounded returns and excess returns versus SPY
- [x] Mark partial years and count full years outperforming SPY
- [ ] Validate diagnostics against 2003–2026 real local results

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
