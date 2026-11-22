# AtlasPM

**AtlasPM** is an open-source, local-first portfolio analysis and research toolkit for cash and margin accounts. The long-term roadmap includes AI-assisted allocation, backtesting, broker integrations, and a web dashboard.

> **Status:** v0.4.1 architecture, read-only multi-asset ledger and paper-only risk-control prototype. Still no trained AI or live brokerage connectivity. Analysis and hypothetical stress tests only. No live broker access or trading, and no machine-learning model has been trained yet. Not investment advice.

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

## Example structure

```text
AtlasPM/
├── .github/workflows/ci.yml
├── docs/
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
