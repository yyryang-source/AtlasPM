# AtlasPM Institutional Portfolio Management System — target design

**Status:** Architecture target, not a claim that the existing AtlasPM is institution-ready. The current v0.3.1 model is a historical research prototype and has no trained AI, live brokerage account synchronization or trading.

## Product architecture

AtlasPM will combine a portfolio management system (PMS), central risk management system (RMS), options hedging, US Treasury collateral management, financing and order management (OMS). It must support separate cash and margin accounts and a combined risk view without falsely treating the assets of separate brokerage accounts as freely cross-collateralized.

### Seven systems and their independent authority

1. **Research and AI**: point-in-time price, valuation, factors, macro features; ML may rank opportunities and explain assumptions but NEVER approve or transmit orders.
2. **Portfolio optimizer**: cash, stocks/ETFs, T-Bills/Notes/Bonds and option overlays subject to turnover, concentration, leverage, beta and liquidity bounds.
3. **Position sizing and rebalancing**: target-vs-actual calculation, cash and financing budgets, additions, reductions, cool-downs, price drift and trade cost. Always construct an explicit pro-forma portfolio after planned transactions.
4. **Central pre/intra/post trade RMS**: VaR, Expected Shortfall, maximum drawdown, market/factor exposure, option Greeks, stressed margin surplus, collateral haircuts, stale-market-data and counterparty/system faults. Deterministic hard limits override model recommendations.
5. **Options hedge engine**: Protective Put, Put Spread and Collar; strike/maturity selection, Delta/Gamma/Vega/Theta/Rho, premium budget, scenario repricing, coverage ratio, hedge liquidity, expiry, exercise, assignment and roll policies. No uncovered short option writing by default.
6. **Treasury funding and collateral**: security master by CUSIP, maturity/coupon, dirty/clean price, duration/DV01, coupon and maturity schedule, margin eligibility, broker haircuts, encumbrance, conservative available loan value and borrowing spread.
7. **OMS, broker adapters and dashboard**: audit log, paper fills, order state machine, reconciliation, historical and live risk panels, execution approvals, and IBKR account/margin data. Live capability must remain separately disabled unless explicitly authorized and engineered.

## Account and funding invariants

- Cash and margin accounts get distinct cash, settlement, position, accrued expenses, debt and option-liability ledgers.
- Net liquidation equity must reconcile to positions plus cash plus receivables minus debt and other liabilities.
- Gross market exposure, available funds, excess liquidity, net liquidation, initial margin, maintenance margin and buying power are distinct fields; they must never be treated as interchangeable.
- A marginable security contributes to buying power only to the extent the actual brokerage account and security-specific treatment permit.
- Collateral should have a status of ELIGIBLE, INELIGIBLE or UNKNOWN. UNKNOWN **cannot** generate buying power in an order-sizing decision.
- Broker-calculated margin and order-preview results are more authoritative than our hypothetical margin estimate; independent stress tests should still impose additional conservative guardrails.
- A Treasury bond should have yield and accrued interest, rate and duration risk, maturity and coupon events, a broker-dependent haircut, and scenario collateral value. The target system must not double-count the same bond as unrestricted cash and financed collateral.
- A Treasury repo desk is NOT the same funding facility as a retail margin debit in a brokerage account. Do not label them as equivalent.

### Broker reference requirements

IBKR publishes U.S. Treasury margin requirements that depend on maturity (its published U.S. table lists 1% for less than six months, 2% for less than one year, 3% for one to three years, 4% for three to five, 5% for five to ten, 7% for ten to twenty and 9% for twenty-plus years). These are **margin requirements on the bond**, not a promised dollar-by-dollar borrowing facility for a stock/option combination. The broker may raise house requirements and reduce collateral value.

Source: https://www.interactivebrokers.com/en/trading/margin-bonds.php

An external T-Bill transfer to IBKR may have its own minimum eligibility requirement (IBKR FAQ currently mentions at least $1 million transferred). That is separate from buying Treasury securities inside an account.

Source: https://www.interactivebrokers.com/support/faq/content/deposit-us-treasury-bills-as-77064278

### Options protection lifecycle

Protective puts protect only their defined underlying payoff below strike and require a premium; a put spread reduces premium but caps further downside protection; collars sell away some upside and introduce short-option assignment risk. An index/ETF hedge against a concentrated single-stock portfolio must explicitly model hedge basis and portfolio beta; static Delta is not reliable protection during large gaps.

No live hedge may be recommended as immediately executable unless chain timestamps, bid/ask, contract multiplier, instrument identity, permissions, cash premium, spread max loss, assignment/expiry treatment and post-trade margin implications are all confirmed. Synthetic options pricing is allowed for *simulation only*, labeled accordingly.

## Risk-state machine and order privileges

| State | Example trigger | Allowed behavior |
|---|---|---|
| NORMAL | Fresh reconciled data and adequate stress liquidity | Controlled additions or reductions only after order risk gates |
| WATCH | Early drawdown, volatility, margin buffer or concentration warning | Block discretionary leverage increases, review hedge plan |
| DE_RISK | Exposure / tail stress / liquidity policy breached | Propose reductions, repay financing, evaluate eligible protective hedges |
| CRITICAL | Deficit, severe unreconciled positions or risk engine failure | Cancel exposure-increasing pending orders, prevent new risk, operator alerted |
| HALT | Kill switch, unknown order outcome, lost broker session | Freeze automated submissions; reconcile before any retry |

Use configurable thresholds with hysteresis, re-entry criteria, minimum dwell time and no uncontrolled risk-on / risk-off oscillation. A historical price drop alone does not authorize automatic buying.

A proposed trade receives one of ALLOW, BLOCK, REDUCE_ONLY, HEDGE_REVIEW or HALT. The AI cannot bypass these decision objects.

## Unified execution workflow

~~~text
Market + fundamentals + yields + options chains + broker snapshots
                            |
                 Time-stamped data validation
                            |
                  Unified reconciled ledger
                            |
            AI research + deterministic optimizer
                            |
            Target allocation / add / reduce / hedge
                            |
             Combined pro-forma portfolio risk gate
               / stock / treasury / option / debt
                            |
         Broker What-If order preview + permissions
                            |
                  Paper OMS by default
                            |
             Explicit operator approval for LIVE
                            |
             Broker API execution + fill tracking
                            |
                Reconciliation / alerts / audit
~~~

For IBKR the Web API documentation includes a What-If preview endpoint reporting commission, equity impact, initial margin and maintenance margin impact. This is necessary to cross-check predicted risk, but should not replace our stress limits. The endpoint that actually places orders must be physically isolated from research and disabled by default.

Sources:
- https://www.interactivebrokers.com/docs
- https://www.interactivebrokers.com/docs/web-api/v1/endpoints/orders/preview-order-what-if-order

## Code organization

- src/atlaspm/domain: security identifiers, instruments, accounts, lots, cash/debt ledgers, trade intents and typed risk observations.
- src/atlaspm/data: market data adapters, timestamp freshness, point-in-time datasets, corporate actions, interest and Treasury calendars.
- src/atlaspm/research: factor returns, forecasts, model registry, data lineage and drift.
- src/atlaspm/allocation: multi-asset optimization, turnover and transaction construction.
- src/atlaspm/risk: multi-asset stress, Greeks, tail risk, liquidity, account margin and central policy.
- src/atlaspm/collateral: CUSIP master, Treasury schedules, DV01, haircuts and approved funding availability.
- src/atlaspm/hedging: option contract lookup, scenario repricing, protection and hedge roll calculations.
- src/atlaspm/brokers: normalized broker position/margin preview and order interfaces, with a separate paper-only adapter.
- src/atlaspm/oms: unique client order IDs, audit and immutable events, idempotent fills, cancels, replace and reconciliation.
- src/atlaspm/monitoring: system state, kill switch, report, alerting and API health.
- UI: authenticated dashboard, separate account risk screens and aggregate views; backend retains all secrets.

## Engineering and safety requirements

1. Deterministic policy checks must be testable without internet or any language model.
2. No lookahead or survivor bias in claimed backtests; validate on held-out market regimes and historical universes.
3. Every state-changing event must be auditable and reproducible: funding, accrued interest, option expiry/assignment, Treasury coupon/maturity, execution fees and adjustments.
4. CI checks should include accounting invariants, stale quote rejection, option payoff and unit consistency, catastrophic market stress, financing and liquidation cases.
5. No API secrets, real account IDs or investor transaction logs in public GitHub. Local Windows PowerShell development remains the default.
6. Paper-trading before any possible real orders; test idempotency and reconciliation failures, permission boundaries and human approvals.
7. Determine any legal, regulatory and fiduciary obligations before marketing or distributing an automated advisory/trading product.

**This is a target engineering specification, not a claim that an investment bank has reviewed or certified AtlasPM, and not personalized investment advice.**
