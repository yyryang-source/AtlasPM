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

## v0.2.5 — pre-trade research risk budgets (implemented; awaiting full Windows validation)

- [x] Read-only candidate portfolio volatility, concentration, leverage, stress loss, maintenance cushion and financing-cost checks
- [x] Report constrained hypothetical leverage cap or infeasibility of any fully invested 1× candidate
- [x] Offline core and CLI regression tests; source estimates from historical covariance
- [ ] Integrate past-only risk limits inside the walk-forward rebalancing process
- [ ] Add cash allocations under 1× and data-driven drawdown-control experiments

## v0.3.0 — dynamic past-only risk budgets (implemented; awaiting full local validation)

- [x] Independent opt-in `backtest-risk` engine; original baseline results remain unchanged
- [x] Window-local sample volatility to cap leverage at each quarter's rebalance
- [x] Cash allocation for modeled exposure below 1×; borrowing accounting above 1×
- [x] Stress-loss, hypothetical maintenance-buffer, fee, APR and concentration budgets
- [x] Read-only signals one close before execution; explicit cash/borrow/debt ledger reconciliation
- [x] Offline regression and CLI smoke tests
- [ ] Validate 2003–2026 risk-managed results locally against baseline and SPY
- [ ] Test sensitivity across genuinely unseen time periods and independent symbol universes
- [ ] Replace historical fixed APR and simple liquidation rules with realistically sourced financing schedules and conservative fills

## v0.3.1 — historical tail-risk budgeting (implemented; awaiting Windows validation)

- [x] Empirical one-day worst-tail Expected Shortfall estimator from prior returns
- [x] Optional `--max-daily-tail-loss` cap on risky exposure at scheduled rebalances
- [x] Preserve baseline behavior with the flag omitted
- [x] Report which periods the ES cap binds, and log the training sample and tail count
- [x] Offline regression tests for numeric calculations, input validation and leakage
- [ ] Compare 2003–2026 outcomes with an unchanged v0.3.0 baseline and SPY
- [ ] Out-of-sample model selection and direct CVaR asset-weight optimization

## v0.4.0 — institutional architecture contracts (committed; needs Windows validation)

- [x] Product architecture: PMS/RMS, cash/margin accounts, Treasury collateral, options overlay and OMS
- [x] Phase-by-phase implementation roadmap with testable operational requirements
- [x] Pure fail-closed intent preflight for paper-only proposals and broker preview inputs
- [x] Cash borrowing, unknown Treasury credit, stale quote, options permissions and margin tests
- [ ] Integrate real broker read-only accounts and trustworthy What-If data via separate adapter
- [ ] Add Treasury CUSIP cashflow accounting, options contracts and a full order/event ledger
- [ ] Expand into controlled paper OMS and replayable reconciliation before any live access

## v0.4.1 — read-only institutional multi-asset ledger (committed; awaiting Windows validation)

- [x] Synthetic cash and margin accounts with settled cash, margin debt and financing accrual
- [x] Equity/ETF share marks, US Treasury dirty price and first-order duration DV01
- [x] Signed option position market values and short liability flags
- [x] Independent account net-liquidation reconciliation, quote staleness and USD-only validation
- [x] Broker-sourced Treasury collateral value reported but never added to cash or equity
- [x] Offline regression tests and PowerShell ledger-report command
- [ ] Treasury coupon settlement journal and real broker read-only ingestion
- [ ] Option expiry/assignment/payoff engine, plus robust broker margin validation

## v0.4.2 — read-only account-aware equity add/reduce planner (implemented; awaiting Windows validation)

- [x] Paper-only BUY / SELL of existing long stock/ETF positions with client-order intent IDs
- [x] Pro-forma settled cash, unsettled liability reserve, debt, shares and net liquidation
- [x] Match mock broker What-If preview to account, identifier, side, quantity, price and financing debt
- [x] Reject stale marks, mismatched ledger/broker NLV, understated gross and stress exposures
- [x] Integrate centralized institutional risk gate and block orders on unreconciled accounts
- [x] Synthetic fixtures, offline regression tests and CLI smoke workflow
- [ ] Multi-order portfolio target rebalancer with held-out cost data and trade event journal
- [ ] Broker-authenticated account and What-If adapters; live orders remain disabled

## v0.4.3 — combined multi-asset basket proposals (committed; pending Windows test run)

- [x] One-account stock BUY/SELL, existing Treasury face purchases, new long protective puts and debt repayments
- [x] T+1-style settlement-aware receivables: stock-sale cash is not available inside the same basket
- [x] No automatic increment to broker Treasury collateral credit
- [x] Independent cash/debt/position mark-to-market reconciliation including modeled fees and limit/mark differences
- [x] Per-leg fail-closed central RMS reviews and exact ordered-leg mock preview matching
- [x] Protective puts limited to held long underlying share coverage; no assumed stress profit credited for hedges
- [x] Local example and CLI, regression tests and network-free GitHub Actions smoke run
- [ ] Broker-authenticated What-If requests, order journal/partial fills and real options/UST data

## v0.4 — optimization and AI experiments

- [ ] CVaR optimizer and turnover-aware, broker-compatible risk constraints
- [ ] Out-of-sample baselines before training
- [ ] Local ML training on Windows with reproducible seeds/configs
- [ ] Leakage-safe validation and model cards; no unsupported return promises

## v0.5 — broker sandbox

- [ ] Read-only positions and balances adapter
- [ ] Broker-supported simulated/paper order routing
- [ ] Reconciliation, audit logs, rate limits, idempotency
- [ ] Live trading permanently off by default; explicit permissions and safety review required

## v0.6 — interface

- [ ] Browser dashboard for balances, portfolios and risk explanations
- [ ] Scenario comparison, cash/margin account selection and approval workflow

Priority: make the accounting auditable before adding forecasting or automated execution.
