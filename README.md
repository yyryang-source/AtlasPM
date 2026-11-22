# AtlasPM

**AtlasPM** is an open-source, local-first portfolio analysis and research toolkit for cash and margin accounts. The long-term roadmap includes AI-assisted allocation, backtesting, broker integrations, and a web dashboard.

> **Status:** v0.1 foundation. Analysis and hypothetical stress tests only. No live broker access or trading, and no machine-learning model has been trained yet. Not investment advice.

## What works today

- Model long-only cash and margin accounts separately.
- Calculate equity, borrowed balance, gross exposure, leverage, maintenance margin and excess.
- Stress test uniform market declines for potential margin deficits.
- Estimate simple annualized borrowing expense given an input APR.
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
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The same commands work on Python 3.14 for this standard-library-only phase. When numerical/ML libraries are added, development will use a pinned compatible Python and dependency lock.

## Example structure

```text
AtlasPM/
├── .github/workflows/ci.yml
├── docs/
│   ├── ARCHITECTURE.md
│   ├── RISK_POLICY.md
│   └── ROADMAP.md
├── examples/portfolio.json
├── src/atlaspm/
│   ├── __init__.py
│   ├── cli.py
│   ├── models.py
│   ├── optimizer.py
│   └── portfolio.py
├── tests/
├── pyproject.toml
└── SECURITY.md
```

## Important distinctions

This project does **not** connect to a broker in v0.1. A maintenance rate supplied in the example is only an **illustrative assumption**; a broker's house requirements, concentration rules and liquidation timing can change without notice. The model does not predict actual margin calls or execution. Market stress is simplified (all positions move down by the same fraction, cash and debt held constant).

Do not commit credentials, account numbers, personal transaction histories, or confidential investment data. Contributions and architecture details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

MIT licensed.
