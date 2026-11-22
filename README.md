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
.\.venv\Scripts\python.exe -m atlaspm.cli download-prices --symbols QQQ NVDA JPM KO --start 2021-01-01 --end 2026-11-22 --output local_data\prices.csv
.\.venv\Scripts\python.exe -m atlaspm.cli estimate --file local_data\prices.csv --output local_data\estimates.json
.\.venv\Scripts\python.exe -m atlaspm.cli optimize --file local_data\estimates.json --risk-aversion 4 --max-weight 0.6 --leverage 1
```

This uses Yahoo Finance via optional yfinance as a **research data source**, saves data **locally** (ignored by Git), aligns common price dates without forward-filling, and computes annualized historical sample means and covariance. Historical sample means are **not forecasts**. See [data methodology and limitations](docs/DATA.md).

## Example structure

```text
AtlasPM/
├── .github/workflows/ci.yml
├── docs/
│   ├── DATA.md
│   ├── ARCHITECTURE.md
│   ├── RISK_POLICY.md
│   └── ROADMAP.md
├── examples/portfolio.json
├── examples/optimizer.json
├── src/atlaspm/
│   ├── __init__.py
│   ├── cli.py
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
