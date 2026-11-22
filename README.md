# AtlasPM

**AtlasPM** is an open-source, local-first portfolio analysis and research toolkit for cash and margin accounts. The long-term roadmap includes AI-assisted allocation, backtesting, broker integrations, and a web dashboard.

> **Status:** v0.2 early research prototype. Analysis and hypothetical stress tests only. No live broker access or trading, and no machine-learning model has been trained yet. Not investment advice.

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

## New: SPY benchmark and margin deleveraging stress mode (v0.2.3)

The 2003–2026 example has highlighted potential maintenance breaches under hypothetical 2× borrowing. The new mode allows a **next-close, full deleveraging to 1×** stress scenario. This is **not** Interactive Brokers' liquidation algorithm.

```powershell
# 2× with a one-session delayed deleveraging rule after a simulated breach
.\.venv\Scripts\python.exe -m atlaspm.cli backtest --file local_data\prices.csv --lookback-days 756 --rebalance-days 63 --risk-aversion 4 --max-weight 0.6 --leverage 2 --apr 0.065 --fee-bps 5 --maintenance 0.30 --margin-policy next_close_deleverage_to_1x --output local_data\backtest_margin_forced.json

# Download independent SPY prices WITHOUT changing the optimizer universe
.\.venv\Scripts\python.exe -m atlaspm.cli download-prices --symbols SPY --start 2000-01-01 --end 2026-11-23 --output local_data\spy_prices.csv

# Compare with existing cash and margin scenario
.\.venv\Scripts\python.exe -m atlaspm.cli benchmark-spy --cash local_data\backtest_cash.json --margin local_data\backtest_margin_forced.json --spy local_data\spy_prices.csv --output local_data\spy_comparison_forced.json
```

See [hypothetical margin stress rules](docs/MARGIN_SIM.md) and [independent SPY benchmark](docs/BENCHMARK.md). Neither feature validates live trading or eliminates survivorship bias.

## Example structure

```text
AtlasPM/
├── .github/workflows/ci.yml
├── docs/
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
│   ├── benchmark.py
│   ├── audit.py
│   ├── backtest.py
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
