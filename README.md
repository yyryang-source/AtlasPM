# AtlasPM

**AtlasPM** is an open-source, local-first portfolio analysis and research toolkit for cash and margin accounts. The long-term roadmap includes AI-assisted allocation, backtesting, broker integrations, and a web dashboard.

> **Status:** v0.6.0 first ORIGINAL AtlasPM architecture (Atlas RegimeNet v1): a from-scratch trained three-expert, dual-risk-and-return multiasset model with custom loss, allocation preference, and independent hard risk gates. Previous Ridge, LightGBM, Qlib-like model-family experiments remain benchmarks only. All model fits/rebalances are offline 0–1x cash-only and NOT validated for profit or live IBKR execution. Not investment advice.

## New in v0.4.9: Offline IBKR financing and margin checks

Use `ibkr-financing-check` with an IBKR read-only capture, a manually reviewed conid manifest, and a separately supplied current securities-segment margin/financing statement. The tool checks loan principal claim differences, the Gateway NLV, initial/maintenance formulas, margin cushion, account IDs, and snapshot timestamps. Output is private under `local_data/`. **Every result remains non-executable and unverified:** neither cross-checked numbers nor manually supplied source labels establish authentic broker margin buying power.

See [v0.4.9 PowerShell commands and accounting limits](docs/IBKR_FINANCING.md).

## v0.4.10: Read-only IBKR margin monitoring

`ibkr-margin-monitor` reads an authenticated **local** IBKR Client Portal Gateway `GET /portfolio/{accountId}/summary` (or entirely synthetic fixture) and checks securities-segment NLV, EquityWithLoanValue, initial/maintenance margin, available funds, excess liquidity, account IDs, currency restrictions, timestamps, and basic margin risk buffers. It reports redacted `NORMAL`, `WATCH`, `CRITICAL`, or `BLOCKED_DATA` states. No live margin financing principal is independently verified, and no broker/paper orders can be placed.

Run the quick start and read limitations in [v0.4.10 Margin Monitor](docs/IBKR_MARGIN_MONITOR.md).

## v0.4.11: Hypothetical margin shock and financing cost

The new `ibkr-stress` CLI ingests **private** saved v0.4.10 margin monitoring and v0.4.9 financing-claim diagnostics, requires explicit scenario assumptions and estimates illustrative NLV / equity-with-loan, margin requirements, available funds, excess liquidity and simple future borrowing cost. It never treats a claimed loan as verified, never double-subtracts existing debt from NLV, and permanently blocks order execution and institutional risk-gate entry. It does **not** implement IBKR's liquidation algorithm.

Follow the [synthetic Windows PowerShell test procedure](docs/IBKR_MARGIN_STRESS.md).

## v0.4.12: Multiasset correlation and financing risk budget

The `ibkr-multiasset-budget` CLI uses the private prior `ibkr-stress` output and a new synthetic-only research-sleeve manifest. It computes explicitly supplied per-asset shock P&L, a 99% one-day parametric normal VaR under validated positive-semidefinite correlation matrices, simple assumed borrowing interest, and illustrative margin/buffer/concentration budget alerts. Correlation affects statistical VaR but does **not** change user-specified deterministic shocks. No AI model is trained; no brokerage orders are possible, and every trust/readiness flag stays false.

See [v0.4.12 Windows instructions and assumptions](docs/IBKR_MULTI_ASSET_BUDGET.md).

## v0.4.13: Reverse margin and liquidity buffer stress

The new `ibkr-reverse-stress` CLI consumes the private v0.4.12 synthetic budget output and assumed interest, maintenance and correlated shock sensitivities. It analytically finds the hypothetical market decline at which a stated liquidity-buffer floor, zero maintenance excess or zero NLV is reached. Results distinguish already-at-threshold, within-range and not-reached states; they **do not** predict IBKR liquidation prices and cannot grant broker risk permissions.

See [v0.4.13 PowerShell commands and modeling boundaries](docs/IBKR_REVERSE_STRESS.md).

## v0.4.14: Private risk evidence-chain audit

The new `ibkr-risk-audit` command reviews five saved offline reports (margin monitor, financing claims, static stress, multiasset budget and reverse stress), checks account identity, cross-stage amounts and scenario arithmetic, counts hypothetical warnings, and optionally compares unsigned SHA-256 local-file digests against a previous audit. It never independently authenticates broker provenance or grants order permission; even a mathematically consistent result remains a hypothetical report.

See [v0.4.14 PowerShell walkthrough and evidence caveats](docs/IBKR_RISK_CHAIN_AUDIT.md).

## v0.4.15: Offline risk audit differences

The `ibkr-audit-diff` command compares two private v0.4.14 audit JSON files for the same account, detects SHA-256 local source-fingerprint changes and hypothetical alert-count shifts, and reports review-required states for inconsistent or changed inputs. A decline in simulated alerts is **not** verification of risk reduction. All inputs/output remain in `local_data/` and the CLI displays a redacted summary.

See [v0.4.15 PowerShell audit comparison workflow](docs/IBKR_RISK_AUDIT_DIFF.md).

## v0.4.16: Offline browser-based risk dashboard

Run `ibkr-dashboard` to create `local_data/atlaspm_dashboard.html`, then open the standalone file in a Windows browser. The dark, responsive interface summarizes five saved risk stages, hypothetical scenario counts, local audit warnings and report-diff diagnostics. It embeds **only masked state labels**—not full account details, financing balances, asset names, private scenario names or SHA-256 digests. The HTML has no scripts, remote assets, server, API calls or trading function.

See [v0.4.16 Windows PowerShell quick start](docs/IBKR_DASHBOARD.md).

## v0.5.1: Windows path privacy and ML test compatibility fix

The native Windows CLI test now inspects parsed JSON instead of trying to remove a raw absolute path from escaped JSON output. The `ml-rebalance-backtest` console `written` field now always reports a project-relative `local_data/` path (no Windows username or absolute path), while complete trained-model data remain private. GitHub CI now includes a Windows Python 3.13/PowerShell regression job as well as the existing Python 3.12–3.14 Linux test matrix. No changes to the trained forecasting model or trading permissions.

## v0.5.0: First trained AI/ML dynamic portfolio rebalancing baseline

The new `ml-rebalance-backtest` CLI fits **supervised ridge-regression risk forecasts** independently for every asset, using a strict past-only training timeline, and automatically rebalances the hypothetical portfolio across assets and cash at each simulated rebalance date. Exposure is capped by predicted volatility, historical daily tail losses, market shock budgets, and account drawdown limits. It compares trained ML to an otherwise identical untrained trailing-volatility allocator and an equal-weight buy-and-hold portfolio, including fee and slippage assumptions.

This is the first trained, fully **offline** research model, *not* a proven profitable strategy, real IBKR paper order system or live AI investment manager. Initial gross exposure is restricted to 0–1x with no lending, option positions or actual Treasury coupons. Model coefficients, full decisions, realized historical returns and vendor-specific data are kept private under `local_data/`.

See [v0.5.0 Windows training command, data leakage tests and non-live restrictions](docs/ML_DYNAMIC_REBALANCE.md).

## v0.5.2: Train open-source LightGBM dynamic allocation forecasts

The new `--model lightgbm` choice in `ml-rebalance-backtest` uses the **actual optional LightGBM package** to train expanding-window risk forecasts, then sends them through exactly the same historical automatic rebalance, cash, risk-limit and cost calculation pipeline as existing `--model ridge`. LightGBM is also part of Microsoft's Qlib benchmarks, but the AtlasPM implementation is **not** the official Qlib/Alpha158 pipeline or a proprietary/pretrained institutional weight set. All training is offline, no broker/paper/live orders and 0–1x max equity exposure. The final trained native tree model is saved only inside ignored `local_data/`.

Read [verified open-source model references, licenses and PowerShell training commands](docs/OPEN_SOURCE_MODELS.md).

## v0.5.3: Dynamic multi-model selection (champion or ensemble)

The new `ml-model-tournament` command competes **Ridge, native LightGBM and traditional trailing volatility** using only *completed, past-origin* forecast outcomes. A model gets promoted only if its recent validated forward-risk MAE beats traditional risk forecasts by a configured margin, otherwise the system uses the untrained baseline. `--mode champion` picks the leading model, while `--mode ensemble` blends their risk forecasts with dynamic inverse-MAE weights. Both produce **automatic simulated asset/cash rebalances** and report separate shadow performance for each candidate, equal-weight buy/hold, costs, drawdowns, and chronology. No hindsight selection by final portfolio return or Sharpe.

**All validation remains research-only**: no untouched final holdout has been independently certified, and no true broker data, margin or live/paper orders. [Read the model tournament methodology, anti-leak tests and Windows PowerShell commands](docs/ML_MODEL_TOURNAMENT.md).

## v0.6.0: The first AtlasPM ORIGINAL model — not an open-source-model wrapper

`atlas-native-backtest` now trains the internally designed **Atlas RegimeNet v1** directly from your local history. The softmax gate and three asset-aware expert networks jointly learn expected forward realized-risk and forward-return proxies. We implemented training gradients, asymmetric risk-underprediction penalty, an original risk-adjusted allocation score, and optional extra exposure caution, all subordinated to hard concentration, volatility, ES, market shock and drawdown limits.

We are using standard public mathematical operations and optional NumPy **only for arrays**, with no imported fitted LightGBM/Ridge/Qlib model and no proprietary/pretrained checkpoints. The system is still an initial research baseline, not independently proven better than those baselines, nor validated for live orders. Read [native architecture, training command, model ownership claims and no-hindsight limitations](docs/ATLAS_NATIVE_MODEL.md).

## What works today

- Model long-only cash and margin accounts separately.
- Calculate equity, borrowed balance, gross exposure, leverage, maintenance margin and excess.
- Stress test uniform market declines for potential margin deficits.
- Estimate simple annualized borrowing expense given an input APR.
- Download and align historical adjusted daily prices; estimate sample annualized returns/covariance.
- Optimize hypothetical mean-variance allocations from user-supplied expected returns and covariance, with capped weights and assumed financing costs.
- Generate baseline **inverse-volatility** allocations with per-asset concentration caps. This is a non-AI baseline, not an optimal-return guarantee.
- Run past-only rolling-window backtests with turnover costs, simple financing interest, drawdown metrics and a same-universe benchmark.
- Run dependency-light unit tests and GitHub Actions CI.

## Windows PowerShell quick start

Requires Python **3.12 or newer** and Git. The base engine uses only the Python standard library.

```powershell
cd $HOME\Desktop
git clone https://github.com/yyryang-source/AtlasPM.git
cd AtlasPM

py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .

.\.venv\Scripts\python.exe -m atlaspm.cli analyze --file examples\portfolio.json --declines 0.10 0.20 0.30 --apr 0.065
.\.venv\Scripts\python.exe -m atlaspm.cli allocate --vol QQQ=0.24 NVDA=0.50 AAPL=0.30 GOOGL=0.28 --max-weight 0.45
.\.venv\Scripts\python.exe -m atlaspm.cli optimize --file examples\optimizer.json --risk-aversion 4 --max-weight 0.6 --leverage 1
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The same commands work on Python 3.14 for this standard-library-only phase. When numerical/ML libraries are added, development will use a pinned compatible Python and dependency lock.

## Historical data workflow (v0.2)

To replace fictional input assumptions with **observed historical sample statistics**, run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[market]"
.\.venv\Scripts\python.exe -m atlaspm.cli download-prices --symbols QQQ NVDA JPM KO --start 2000-01-01 --end 2026-11-23 --output local_data\prices.csv --overwrite
.\.venv\Scripts\python.exe -m atlaspm.cli estimate --file local_data\prices.csv --output local_data\estimates.json
.\.venv\Scripts\python.exe -m atlaspm.cli optimize --file local_data\estimates.json --risk-aversion 4 --max-weight 0.6 --leverage 1
```

The default example requests 2000–2026 prices, including the dot-com bust, financial crisis and later market cycles, when historical data is available. It is important to compare recent and rolling windows rather than assuming all historical regimes predict the future.

This uses Yahoo Finance via optional yfinance as a **research data source**, saves data **locally** (ignored by Git), aligns common price dates without forward-filling, and computes annualized historical sample means and covariance. Historical sample means are **not forecasts**. See [data methodology and limitations](docs/DATA.md).

## Walk-forward backtesting (research prototype)

A **past-only rolling** backtest is available. This avoids fitting once on the whole 2000–2026 dataset and calling those fitted results realized performance:

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli backtest --file local_data\prices.csv --lookback-days 756 --rebalance-days 63 --risk-aversion 4 --max-weight 0.6 --leverage 1 --fee-bps 5 --output local_data\backtest_cash.json
```

The command prints a summary and saves full daily-equity and rebalance histories locally. It compares against **same-universe equal-weight buy-and-hold**, not SPY, and is a simulation with simplified fees, financing and execution. See [walk-forward methodology](docs/BACKTEST.md) before interpreting its performance.

## Compare cash and margin backtests (risk audit)

Once both `backtest_cash.json` and `backtest_margin_2x.json` exist, run:

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli audit --cash local_data\backtest_cash.json --margin local_data\backtest_margin_2x.json --output local_data\risk_audit.json
```

The audit compares annualized returns, volatility and drawdowns, highlights benchmark underperformance and **shows every simulated maintenance breach date**. It refuses mismatched simulations and explicitly labels results as research only. [Risk audit documentation](docs/AUDIT.md).

## New: SPY benchmark and margin deleveraging stress mode (v0.2.4)

The 2003–2026 example has highlighted potential maintenance breaches under hypothetical 2× borrowing. The new mode allows a **next-close, full deleveraging to 1×** stress scenario. This is **not** Interactive Brokers' liquidation algorithm.

```powershell
# 2× with a one-session delayed deleveraging rule after a simulated breach
.\.venv\Scripts\python.exe -m atlaspm.cli backtest --file local_data\prices.csv --lookback-days 756 --rebalance-days 63 --risk-aversion 4 --max-weight 0.6 --leverage 2 --apr 0.065 --fee-bps 5 --maintenance 0.30 --margin-policy next_close_deleverage_to_1x --output local_data\backtest_margin_forced.json

# Download independent SPY prices WITHOUT changing the optimizer universe
.\.venv\Scripts\python.exe -m atlaspm.cli download-prices --symbols SPY --start 2000-01-01 --end 2026-11-23 --output local_data\spy_prices.csv

# Compare with existing cash and margin scenario
.\.venv\Scripts\python.exe -m atlaspm.cli benchmark-spy --cash local_data\backtest_cash.json --margin local_data\backtest_margin_forced.json --spy local_data\spy_prices.csv --output local_data\spy_comparison_forced.json
```

The SPY comparison now also estimates **historical Beta, correlation, tracking error, information ratio and annual calendar-year excess returns**. Detailed yearly rows stay in `local_data/spy_comparison_forced.json`, while the CLI prints a compact summary. The figures are descriptive, not proof of alpha.

See [hypothetical margin stress rules](docs/MARGIN_SIM.md) and [independent SPY benchmark](docs/BENCHMARK.md). Neither feature validates live trading or eliminates survivorship bias.

## Pre-trade portfolio risk budget (v0.2.5)

The new read-only `risk-check` command evaluates a **hypothetical** optimized portfolio against leverage, name concentration, modeled annual volatility, financing expense and a uniform market-crash scenario:

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli risk-check --file local_data\estimates.json --leverage 2 --risk-aversion 4 --max-weight 0.6 --max-name-weight 0.6 --max-volatility 0.30 --stress-decline 0.30 --max-stress-loss 0.40 --maintenance 0.30 --min-buffer 0.05 --output local_data\risk_budget_2x.json
```

If the modeled 2× candidate fails, it reports **which constraints failed** and whether any fully invested exposure of 1× or more could satisfy the assumptions. This is **not** broker-approved buying power and does not change live orders or past backtests. See [risk-budget methodology](docs/RISK_BUDGET.md).

## Dynamic cash and margin risk budgets (v0.3.0)

**New opt-in past-only backtest:** instead of simply detecting a 2× leverage breach after the fact, the engine caps gross exposure at every rebalance based on *then-available* sample covariance, a volatility limit, a market-crash loss budget, a maintenance buffer, and annual interest costs. It can also hold cash (gross exposure below 1×). The original `backtest` command remains unchanged.

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli backtest-risk --file local_data\prices.csv --lookback-days 756 --rebalance-days 63 --risk-aversion 4 --max-weight 0.60 --max-leverage 2 --max-volatility 0.30 --stress-decline 0.30 --max-stress-loss 0.40 --maintenance 0.30 --min-buffer 0.05 --apr 0.065 --max-financing-cost 0.08 --fee-bps 5 --output local_data\backtest_risk_managed.json

.\.venv\Scripts\python.exe -m atlaspm.cli benchmark-spy --cash local_data\backtest_cash.json --margin local_data\backtest_risk_managed.json --spy local_data\spy_prices.csv --output local_data\spy_comparison_risk_managed.json
```

The second `--margin` parameter is merely the name of the second comparison case; the adaptive strategy may use no margin at many dates. **Do not interpret in-sample risk rules or results as future Alpha.** Read [adaptive risk methodology](docs/ADAPTIVE_RISK.md) and the strategy's caveats.

## Optional rolling historical tail-loss budget (v0.3.1)

Extend `backtest-risk` with an empirical historical **one-day 5%-tail Expected Shortfall** bound:

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli backtest-risk --file local_data\prices.csv --lookback-days 756 --rebalance-days 63 --risk-aversion 4 --max-weight 0.60 --max-leverage 2 --max-volatility 0.30 --stress-decline 0.30 --max-stress-loss 0.40 --maintenance 0.30 --min-buffer 0.05 --apr 0.065 --max-financing-cost 0.08 --fee-bps 5 --max-daily-tail-loss 0.03 --tail-fraction 0.05 --output local_data\backtest_tail_3pct.json
```

Only past observations are used at each scheduled rebalance. An empirical 3% worst-tail average loss bound is **not** a maximum drawdown or crash-protection guarantee. Omitting `--max-daily-tail-loss` retains the previous behavior. This initial feature scales total exposure, not weights. Read [tail-loss methodology](docs/TAIL_RISK.md).

## Institutional architecture (v0.4.0 contract milestone)

AtlasPM is being redesigned toward an **institutional PMS + RMS + collateral + hedge + OMS** stack. Read [Institutional architecture](docs/INSTITUTIONAL_ARCHITECTURE.md) and the [Phased institutional roadmap](docs/INSTITUTIONAL_ROADMAP.md).

The first integrated building block is a **fail-closed, paper-only trade-intent risk gate**. It handles NORMAL/WATCH/DE_RISK/CRITICAL/HALT states, separate cash/margin account constraints, source freshness and reconciliation, broker-style hypothetical What-If preview, verified-vs-unknown Treasury collateral credits, and protective option premium and permissions. It produces audit reasons; it **never connects to a real broker, fetches real account data or places trades**.

From Windows PowerShell in the repository:

~~~powershell
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli institutional-check --file examples\institutional_scenario.json --output local_data\institutional_preflight.json
~~~

The example is **entirely synthetic**, including its mock broker margin preview and recognized Treasury credit. A local paper-intent approval is *not* broker authorization or investment advice. Real-world trading remains disabled.

## Institutional Multi-Asset Ledger (v0.4.1)

The new read-only institutional ledger supports **separate cash and margin accounts**, long stock/ETF positions, **US Treasury clean + accrued-interest dirty valuation**, signed option positions, financing debt and broker-net-liquidation reconciliation. It records whether Treasury collateral has broker-confirmed informational credit without double counting that credit as cash.

~~~powershell
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli ledger-report --file examples\institutional_ledger.json --output local_data\institutional_ledger_report.json
~~~

The sample is **100% synthetic**. No broker API is connected. Even a reconciled example ledger is not authorization for actual margin borrowing or options. See [Ledger methodology](docs/INSTITUTIONAL_LEDGER.md).

## Read-only add/reduce order planner (v0.4.2)

The institutional pipeline now connects the **cash/margin accounting ledger** to the central **fail-closed policy gate** for a hypothetical stock/ETF BUY or SELL. It forecasts share count, settled cash, projected borrowing, fees and account net liquidation, while checking an order-bound simulated broker What-If preview.

~~~powershell
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli plan-equity --ledger examples\institutional_ledger.json --plan examples\institutional_equity_plan.json --output local_data\institutional_equity_plan_report.json
~~~

The example consists **entirely of invented broker and price data**. It creates neither live nor paper orders. A fictional ALLOW_PAPER_INTENT response is not real buying-power confirmation. See [equity planning documentation](docs/EQUITY_PLANNER.md) for limitations and safety controls.

## Integrated stock, Treasury, protective-put and margin basket (v0.4.3)

AtlasPM adds a **read-only multi-asset plan-basket** workflow: a single reconciled account can propose a stock sale/add, a purchase of additional face value of an already-held US Treasury, a fully funded **long protective put**, and a margin principal repayment as one coordinated *hypothetical* package. The engine models unsettled sale proceeds, pays premiums from settled cash, maintains debt separately, **never increases Treasury collateral credit automatically**, computes combined pro-forma risk, and requires a mock broker preview tied to the exact batch of legs.

~~~powershell
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli plan-basket --ledger examples\institutional_batch_ledger.json --plan examples\institutional_batch_plan.json --output local_data\institutional_batch_report.json
~~~

Entirely fictional market, brokerage, and order data. An internal paper-intent approval is **not** a broker approval, not a hedge performance promise and does not submit orders. Read [multi-asset basket methodology](docs/MULTI_ASSET_BATCH.md).

## Offline Paper OMS event replay (v0.4.4)

A read-only **paper order state machine and event replay** now sits after the multi-asset preflight. It accepts only a simulated-approved basket and local invented event records, tracks partial fills, rejection, cancel/pending cancel, unknown broker outcomes and settlement events, and independently reconciles portfolio cash, debt, stock, Treasury and long-put holdings after each event.

~~~powershell
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli oms-replay --ledger examples\institutional_batch_ledger.json --plan examples\institutional_batch_plan.json --events examples\paper_oms_events.json --output local_data\paper_oms_report.json
~~~

All events and brokerage previews are **entirely fictional**. This is NOT a connection to real IBKR paper trading and NEVER submits an order. See [Paper OMS design](docs/PAPER_OMS.md).

## After-fill portfolio guard and replay audit (v0.4.5)

Paper OMS now measures conservative gross leverage and equity/Treasury stress **after every synthetic fill**, and halts subsequent replay if an intermediate risk budget is breached. It also saves a reproducible, unsigned SHA-256 event hash chain to help detect edits to local audit results. The prior 13-event example should continue to pass all static risk checks without changing its accounting.

~~~powershell
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli oms-replay --ledger examples\institutional_batch_ledger.json --plan examples\institutional_batch_plan.json --events examples\paper_oms_events.json --output local_data\paper_oms_report.json
~~~

Both features operate **offline with fabricated broker data**. A hash chain is not a signature, and static post-fill limits are not a broker margin recheck. No real or broker-paper orders are placed. See [OMS risk and audit notes](docs/OMS_RISK_AUDIT.md).

## Offline broker-export versus portfolio-ledger reconciliation (v0.4.6)

The latest **read-only broker-reconcile** command compares separately supplied account/position reports with the local multi-asset ledger. It checks cash, financing principal and interest, net liquidation, equity shares, Treasury face value, options contracts and terms, mark-to-market values, **reported** collateral credits and snapshot freshness. It detects missing/extra holdings and mismatched imported balance-sheet arithmetic.

~~~powershell
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli broker-reconcile --ledger examples\institutional_batch_ledger.json --snapshot examples\broker_export_synthetic.json --output local_data\broker_reconciliation.json
~~~

The demonstration uses **two invented local JSON files**, NOT an authenticated export from IBKR. A matching result remains explicitly labeled MATCHED_UNVERIFIED_EXPORT; it neither confirms true brokerage buying power nor enables orders. See [broker snapshot reconciliation contracts](docs/BROKER_RECONCILIATION.md).

## IBKR Client Portal Gateway — guarded read-only ingestion (v0.4.7)

AtlasPM now has an **explicit-opt-in GET-only localhost HTTPS adapter** for the IBKR Client Portal Gateway. It can capture a manually authenticated account's raw USD ledger and IBKR contract-ID positions. It never requests credentials, disables TLS checks, submits orders, or turns IBKR balances into automatically approved collateral.

Start with the fully offline synthetic fixture:

~~~powershell
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-readonly --fixture examples\ibkr_gateway_synthetic.json --account DU1234567 --output local_data\ibkr_fixture_capture.json
~~~

Actual IBKR account access is **optional**, requires the user to launch the official local Gateway, log in themselves and explicitly supply --allow-local-gateway with a verified localhost TLS certificate. The complete raw output is private and restricted to the Git-ignored local_data directory, while the console is redacted. **Do not upload real snapshots, broker session information, credentials or account identifiers.** See [IBKR read-only setup and limitations](docs/IBKR_READONLY.md).

## IBKR conid and instrument identity mapping diagnostic (v0.4.8)

The latest local-only command **ibkr-map-check** compares IBKR conids in a previously captured portfolio snapshot with explicitly declared AtlasPM stock, U.S. Treasury and option identities. It blocks unknown contract IDs, ambiguous/non-USD data, stale snapshots, unconfirmed Treasury issuer/bond units and mismatched option terms. It does **not** infer brokerage borrowing, Treasury collateral or accrued interest from signed cash, IBKR's interest rate field, or a bond's asset class.

~~~powershell
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-readonly --fixture examples\ibkr_gateway_synthetic.json --account DU1234567 --output local_data\ibkr_fixture_capture.json
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-map-check --capture local_data\ibkr_fixture_capture.json --mapping examples\ibkr_mapping_synthetic.json --output local_data\ibkr_mapping_check.json
~~~

Both inputs are **entirely fictional**. A completed identity mapping is still marked REVIEW_REQUIRED and can NEVER authorize brokerage risk or trading. The full mapping report stays in Git-ignored local_data and the terminal prints only a redacted summary. Read [IBKR mapping methodology](docs/IBKR_MAPPING.md).

## Example structure

```text
AtlasPM/
├── .github/workflows/ci.yml
├── docs/
│   ├── IBKR_MAPPING.md
│   ├── IBKR_READONLY.md
│   ├── BROKER_RECONCILIATION.md
│   ├── OMS_RISK_AUDIT.md
│   ├── PAPER_OMS.md
│   ├── MULTI_ASSET_BATCH.md
│   ├── EQUITY_PLANNER.md
│   ├── INSTITUTIONAL_LEDGER.md
│   ├── INSTITUTIONAL_ROADMAP.md
│   ├── INSTITUTIONAL_ARCHITECTURE.md
│   ├── TAIL_RISK.md
│   ├── ADAPTIVE_RISK.md
│   ├── RISK_BUDGET.md
│   ├── MARGIN_SIM.md
│   ├── BENCHMARK.md
│   ├── AUDIT.md
│   ├── BACKTEST.md
│   ├── DATA.md
│   ├── ARCHITECTURE.md
│   ├── RISK_POLICY.md
│   └── ROADMAP.md
├── examples/portfolio.json
├── examples/optimizer.json
├── src/atlaspm/
│   ├── __init__.py
│   ├── cli.py
│   ├── ibkr_mapping.py
│   ├── ibkr_gateway.py
│   ├── broker_reconcile.py
│   ├── oms_controls.py
│   ├── paper_oms.py
│   ├── institutional_batch.py
│   ├── institutional_rebalance.py
│   ├── institutional_ledger.py
│   ├── institutional_gate.py
│   ├── tail_risk.py
│   ├── risk_backtest.py
│   ├── risk_budget.py
│   ├── benchmark.py
│   ├── audit.py
│   ├── backtest.py
│   ├── performance.py
│   ├── historical.py
│   ├── mean_variance.py
│   ├── models.py
│   ├── optimizer.py
│   └── portfolio.py
├── tests/
├── pyproject.toml
└── SECURITY.md
```

## Important distinctions

This project does **not** connect to a broker in v0.2. A maintenance rate supplied in the example is only an **illustrative assumption**; a broker's house requirements, concentration rules and liquidation timing can change without notice. The model does not predict actual margin calls or execution. Market stress is simplified (all positions move down by the same fraction, cash and debt held constant).

The mean-variance example uses made-up expected returns and covariance and must **not** be treated as a prediction or a broker margin check. Cash accounts have no margin maintenance requirement.

Do not commit credentials, account numbers, personal transaction histories, or confidential investment data. Contributions and architecture details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

MIT licensed.
