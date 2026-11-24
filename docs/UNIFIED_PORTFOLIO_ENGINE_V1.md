# AtlasPM Phase III — Unified Portfolio Engine V1

## Implemented and audited (main branch, November 23 2026)

| Existing module | Existing behavior | Reuse / gap |
| --- | --- | --- |
| src/atlaspm/quant/optimizer.py | Cap-limited inverse volatility | Reused to seed risk parity; not a true covariance risk parity optimizer |
| src/atlaspm/quant/mean_variance.py | Deterministic fully invested long-only mean variance, PSD input checks | Reused estimator and covariance validator, then constrained sector/turnover solving |
| src/atlaspm/risk/risk_budget.py | Hypothetical volatility, concentration, stress and financing budget | Reused as a risky-sleeve reference inside the separate risk gate |
| src/atlaspm/risk/ibkr_margin_stress.py and other IBKR tools | External-claim cross-checks; broker rules and account financing remain unverified | No fabricated broker authentication; margin *additions* denied |
| src/atlaspm/execution/institutional_ledger.py | Multi-asset cash/debt/mark self-reconciliation | **Used** to construct and validate the synthetic pre-trade ledger |
| src/atlaspm/execution/institutional_batch.py | Exact-leg synthetic What-If and institutional RMS | **Used** as the actual preflight for the proposed basket |
| src/atlaspm/execution/paper_oms.py | Submit/ack/fill/cancel/reject/idempotency, post-fill checks, signed? NO: unsigned hash-chain | **Used** as the only event replay and fill accounting implementation |
| src/atlaspm/monitoring/performance.py | SPY-aligned descriptive active stats | Reused its aligned active-return calculations |
| Factor V1/V2/V3 and AI screener | Exploratory, no independently confirmed PIT identity or model validation | Accepted only as *conceptual producers*, NOT authorized inputs |

### Architecture

Synthetic JSON → strict PIT signal identity/availability/expiry check → existing covariance validation / 7 optional-SciPy optimizers → independent risk/financing gate → target shares with **sell proceeds unsettled** and discrete constraint checks → existing institutional ledger → existing institutional batch RMS → existing Paper OMS events → independent fill/cash/receivable/debt/position reconciliation → descriptive portfolio analytics.

All seven exposed methods support hard single-name/sector/cash/turnover limits, with no silent fallback: `equal_weight`, `risk_parity`, `minimum_variance`, `mean_variance`, `cvar`, `maximum_diversification`, `risk_budgeting`. CVaR uses LP with past-aligned return scenarios (minimum 20). Other optimizers use SciPy SLSQP; failure is an explicit BLOCK. Expected returns and matrices are **supplied assumptions**, not trained or automatically authorized forecasts. Covariance is annualized; CVaR scenario rows are single-period returns. Optimizer output is **provisional** until integer share constraints, available funds, sector and risk policy are rechecked.

All accounts are modeled in USD and long-only. Portfolio equity = settled cash + marked holdings − debt − interest. The conservative turnover limit sums absolute changed stock exposures divided by starting equity (not half-turnover). Margin loans and broker per-security initial/maintenance rates are *not* accepted as verified based on a JSON status field; hypothetical scenario shortfalls are computed but additions are blocked. Independent sell-only research remains available even if the selection/optimization gate fails; it is **not a broker order**.

## Windows PowerShell

Run in a separate terminal; do not stop or edit the market-data backfill:

~~~powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git fetch origin
git switch feature/unified-portfolio-engine-v1
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -e ".[portfolio]"
& $py -m unittest discover -s tests -p "test_unified_portfolio_engine.py" -v
& $py -m atlaspm.cli portfolio-engine --file examples\unified_portfolio_synthetic.json
& $py -m atlaspm.cli portfolio-engine --file examples\unified_portfolio_synthetic.json --output local_data\portfolio_engine\run-001.json
~~~

The second report command refuses overwrite and only writes below `local_data/portfolio_engine`. Run the full regression suite with `& $py -m unittest discover -s tests -v`. No local market-data database files are opened by this engine.

Command: `portfolio-engine --file <synthetic-JSON> [--output <new local_data/portfolio_engine/*.json>]`. All older CLI commands remain registered.

### Input contract

The deterministic example includes `mode: synthetic`, `synthetic_fixture: true`, ISO-8601 timezone-aware `as_of`, a cash or margin `account`, `signals`, `market`, `constraints`, `paper`, and optional aligned `history`.

- Signals require matching `SYN:<symbol>` identifiers with `identity_source: synthetic`; `generated_at`, `data_available_at`, `available_at`, `expires_at`; a version, rank, factor score, sector, research eligibility, synthetic-only source/data-quality tags, and explicitly false training permission. Time order is `data_available_at <= generated_at <= available_at <= as_of <= expires_at`. Stale, future, conflicting or missing fields are rejected.
- The covariance matrix and expected return mapping must align **exactly** with `market.symbols` and the signal order. Annualized covariance is checked for PSD. Optional beta assumptions and per-session scenario returns provide independent risk diagnostics.
- Constraints include optimizer method, `max_name_weight`, sector limits, `min_cash_fraction`, `max_gross_exposure`, `max_turnover`, `min_trade_notional`, `max_single_order_fraction`, volatility, beta, CVaR, drawdown, shock and financing stress limits. Missing requested risk observations fail closed.
- Paper settings allow deterministic fees, slippage and explicitly synthetic `market_impact_bps` (slippage plus impact at most 20 bps) and fill fractions 0–1. A fraction below 1 produces partial fills followed by cancellations; 0 produces rejects. Identical repeated event IDs are checked by the existing OMS idempotency mechanism. Simulated sales create **unsettled receivables**, not buying power.

### Report interpretation

`SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE` means only that the **fictional test fixture** passed all internal reconciliation checks. It does not approve trading, the source market data, model training or margin. `BLOCK` carries reason codes and independent reduction-research candidates. Synthetic preview and broker snapshot values are test doubles, **not independent What-If results**. `research_only=true`, `live_execution_enabled=false`, `broker_order_submission_enabled=false`, and `approved_for_model_training=false` are hardcoded outputs. Payload attempts to override them are rejected.

When aligned synthetic portfolio/SPY/QQQ/equal-weight return series are supplied, analytics include compounded return, CAGR, annual volatility, Sharpe, Sortino, Calmar, maximum drawdown and its duration, arithmetic regression alpha, beta, active statistics, filled turnover, simulated fees, slippage and user-assumed market impact, margin-interest drag and variance/factor-score contributions. These are descriptive synthetic-research statistics; they are NOT tradable backtests, market-calibrated 2008/2020/2022 studies or realized profits.

## Limitations and future integration blockers

1. Existing Paper OMS can buy/sell **only already-held long equity identifiers**. Unknown/new symbols, options and Treasury target allocation are blocked here pending an adapter that extends the old OMS safely.
2. No actual security master, exchange ID mapping, split/delisting/bankruptcy validity or historical universe as-of proof. Synthetic IDs cannot qualify real strategy signals. V1 rejects non-synthetic production inputs; no user-provided boolean can promote live eligibility.
3. No authenticated IBKR buying-power, initial-margin, maintenance-margin, house-rule or What-If verification. Per-security requirements and margin stress are diagnostic only. Margin risk additions blocked; de-risk review independent.
4. No actual dated 2008/2020/2022 scenario path is loaded. Optional `market.historical_scenarios` accepts aligned per-symbol percentage shocks for these labels; outputs remain **unverified supplied scenarios**, never a verified historical replay. Stylized equity shocks are explicitly not historical simulations. No market-data download, no point-in-time backtest approval and no empirical model validation.
5. Portfolio optimizer works on fractional holdings; discretionary integer conversion and funding/cash/sector checks may reject otherwise feasible solutions. SLSQP non-convergence and infeasible constraints always block.
6. Paper OMS is an **offline event replay**: no durable concurrent order reservations, real exchange calendar settlement, execution venue, order acknowledgements or market-impact estimator. Slippage is a user-defined synthetic assumption; true market impact is not claimed.
7. Single-run performance analytics accept optional independent aligned synthetic return series. This one-step OMS does not generate a decades-long daily walk-forward equity history or establish causal alpha.
8. Zero real trading permissions. Before production use, securities identity, point-in-time corporate actions, rights, model validation, per-symbol broker margin, broker reconciliation and robust production OMS all require separate auditable projects.

## Safety / regression tests

`tests/test_unified_portfolio_engine.py` covers cash happy path, existing institutional preflight/OMS integration, partial fills, cancellation, rejection, duplicate journal messages, stale/future/expired/forged signals, conflicting caps, margin requirements unknown, unfunded purchases, missing quotes, mismatched account equity, model-permission override, all seven methods and legacy CLI coexistence.

There is **no automatic merge requirement**: merge only after the GitHub Actions Linux+Windows matrix and full existing regression tests pass. Historical data is never included in any fixture, CI logs or Git commit.
