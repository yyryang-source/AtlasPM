# Historical market data pipeline

This is the first bridge from fictional assumptions to **historical observations**. It is not AI training and does not validate a strategy out of sample.

## Quick start (Windows PowerShell)

From the repository root after git pull:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[market]"
.\.venv\Scripts\python.exe -m atlaspm.cli download-prices --symbols QQQ NVDA JPM KO --start 2000-01-01 --end 2026-11-23 --output local_data\prices.csv --overwrite
.\.venv\Scripts\python.exe -m atlaspm.cli estimate --file local_data\prices.csv --output local_data\estimates.json
.\.venv\Scripts\python.exe -m atlaspm.cli optimize --file local_data\estimates.json --risk-aversion 4 --max-weight 0.6 --leverage 1
```

The download command requires network access. We deliberately **do not download market data in GitHub Actions** because providers have rate limits and external outages. Unit tests use synthetic offline data.

## What is fetched?

- Yahoo Finance daily historical **Close** from the unofficial `yfinance` Python client, explicitly with `auto_adjust=True`. This aims to adjust for dividends/splits using the provider's adjusted close convention.
- `--start` is inclusive and `--end` is **exclusive**. The sample command requests data from 2000-01-01 through the last available trading session before 2026-11-23, subject to provider coverage. --overwrite replaces the previous local CSV after every requested ticker downloads successfully.
- Each ticker is downloaded individually and checked for non-empty usable prices. A missing ticker fails the entire operation instead of silently assigning zero returns.
- The output is a local CSV with header `date,symbol,adjusted_close`.
- `local_data/` is Git-ignored. Do not commit actual portfolio or brokerage exports.

## How the estimates are built

1. Validate prices are positive, finite, unique for (date, symbol), and date-formatted.
2. Align all stocks on the **intersection** of available dates. **No forward fill**.
3. Compute close-to-close daily simple returns.
4. Estimate each asset's **arithmetic annualized sample return** as mean of daily returns × 252. This is **not CAGR**.
5. Estimate **sample** annualized covariance using denominator N-1, multiplied by 252.
6. Store symbols, dates, observation count, and warnings in JSON metadata.

A roughly 27-year sample includes distinct market regimes. Treat the all-history sample as one diagnostic: compare rolling and recent windows, use walk-forward train/test splits, and monitor regime shifts. Historical means can change dramatically with the chosen dates and contain **large estimation error**. Feeding these into a mean-variance optimizer may lead to unstable, overly concentrated allocations. **Do not interpret the resulting weights as a recommendation for live trading.**

## Data caveats

The client is unofficial and source availability, data licensing, adjustment conventions, and API behavior can change. The pipeline does not independently verify every dividend, split, currency change, trading suspension, share-class ticker, survivorship effect, or corporate action. It also does not check portfolio currency denomination.

The estimator uses full-period observations to produce a static in-sample demonstration. **Do not call an optimization generated from these estimates a backtest or predictive AI.** Next: rolling training windows, test windows strictly after their training period, turnover, financing-cost accrual, trading frictions, benchmark comparison, and point-in-time universe.

The example includes `--overwrite` to replace a previous 2021–2026 CSV. Without it, the command refuses to replace an existing CSV.
