# AtlasPM Stock Screener v1 — offline, explainable, non-executable

The `stock-screen` CLI adds **cross-sectional stock selection** to AtlasPM without
changing Atlas FusionNet, the portfolio optimizer, the IBKR risk gate or any
order pathway. It is a deterministic **factor baseline**, not a trained AI
alpha model, a live feed, a recommendation, or a broker trade signal.

## Quick start: Windows PowerShell

From your AtlasPM folder, with Python 3.12+ and its existing `.venv`:

```powershell
git fetch origin
git switch --track origin/feature/stock-screener-v1
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_stock_screener.py" -v

# You may use the existing AtlasPM adjusted-close long CSV if it reaches the cutoff:
.\.venv\Scripts\python.exe -m atlaspm.cli stock-screen --prices local_data\market_2000_2026.csv --as-of 2026-11-20 --top 10 --max-volatility 0.8 --max-drawdown 0.65 --output local_data\stock_screen_2026-11-20.json

$report = Get-Content .\local_data\stock_screen_2026-11-20.json -Raw | ConvertFrom-Json
$report.selected | Select-Object rank,symbol,score,above_sma200 | Format-Table
$report.excluded | Group-Object reason | Select-Object Name,Count
```

If your previous CSV is unavailable, **optionally download real historical prices**
using the project's existing Yahoo research adapter (network access and
`pip install -e ".[market]"` required; Yahoo is unofficial and not guaranteed):

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[market]"
.\.venv\Scripts\python.exe -m atlaspm.cli download-prices --symbols AAPL MSFT NVDA GOOGL AMZN META JPM COST KO --start 2024-01-01 --end 2026-11-21 --output local_data\stock_universe_prices.csv
.\.venv\Scripts\python.exe -m atlaspm.cli stock-screen --prices local_data\stock_universe_prices.csv --as-of 2026-11-20 --top 5 --output local_data\stock_screen.json
```

The end of `download-prices` is **exclusive**; `--as-of` is inclusive.
This yields a ranking only for explicitly supplied symbols, **not the whole
US stock market**. No prices or fundamental values are invented.

## Inputs

Prices CSV is AtlasPM's existing long format:

```csv
date,symbol,adjusted_close
2025-01-02,EXAMPLE_A,100
2025-01-03,EXAMPLE_A,101
```

At least **253 observations per symbol through the cutoff date** are needed.
The last price must be no more than 10 calendar days old by default; 253
observations must fall within 420 calendar days, with no gap over 14 days.
Inputs may include rows after the cutoff; these are ignored.

Optional fundamentals CSV format (the following entries are **fictional
schema examples, not market facts**):

```csv
as_of_date,symbol,sector,market_cap_usd,roe,revenue_growth_yoy,debt_to_equity,fcf_yield,ev_to_ebitda
2026-11-01,EXAMPLE_A,Technology,20000000000,0.25,0.18,0.30,0.06,18
2026-11-01,EXAMPLE_B,Financials,30000000000,0.14,0.05,1.20,0.04,13
```

- `as_of_date` **must mean the first date the complete snapshot was publicly
  available**, not the end of its fiscal quarter and not the date you downloaded it.
- Financial ratios use decimals: ROE 25% = `0.25`, FCF yield 6% = `0.06`.
  All five fundamental factor values are required; EV/EBITDA must be positive.
- Only the latest snapshot for each symbol with `as_of_date <= --as-of` is
  eligible. By default a snapshot older than 200 calendar days is excluded.
- Without `--fundamentals`, **technical-only** scores are computed. Stocks
  missing fundamental snapshots are excluded when fundamentals mode is chosen.
  ETFs generally do not have meaningful company fundamentals; use price-only
  ranking for mixed stock/ETF universes.

Run with licensed, manually prepared, publication-dated financial data:

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli stock-screen --prices local_data\stock_universe_prices.csv --fundamentals local_data\my_published_fundamentals.csv --as-of 2026-11-20 --min-market-cap 10000000000 --max-per-sector 2 --require-uptrend --top 10 --output local_data\stock_screen_multifactor.json
```

This command **does not fetch or infer fundamentals**.

## Factor ranking methodology

All factors are measured only on eligible snapshots **at or before the
cutoff**. Each factor is converted to its cross-sectional midrank percentile,
with ties receiving the same score. Lower volatility, debt/equity and
EV/EBITDA are preferred; higher momentum, SMA strength, ROE, sales growth,
FCF yield and less severe maximum drawdown are preferred.

| Factor | Definition | Price only | With fundamentals |
|---|---|---:|---:|
| 12–1 momentum | adjusted close 21 sessions ago / 252 ago − 1 | 30% | 15% |
| 6–1 momentum | adjusted close 21 sessions ago / 126 ago − 1 | 20% | 10% |
| Price vs SMA200 | latest adjusted close / 200-day mean − 1 | 15% | 10% |
| Annual volatility | sample std of last 63 daily simple returns × sqrt(252) | 20% | 10% |
| Max drawdown | lowest peak-to-trough move in last 253 closes | 15% | 10% |
| Return on equity | user-supplied ROE | — | 10% |
| Revenue growth | user-supplied year-over-year growth | — | 10% |
| Debt/equity | user-supplied D/E | — | 8% |
| FCF yield | user-supplied free-cash-flow yield | — | 10% |
| EV/EBITDA | user-supplied positive multiple | — | 7% |

`score = sum(factor_percentile × weight)`. It is **relative to the input
universe**, not a price target, confidence, forward-return estimate, or
probability of profit. The universe composition matters; industry differences
can make cross-industry fundamental ranks misleading.

**Eligibility filters** run before ranking. The defaults exclude annualized
volatility over 80%, 252-session drawdown worse than −65%, old/sparse prices
and missing/stale fundamentals (when enabled). `--require-uptrend` additionally
requires price >= its SMA200. `--max-per-sector` constrains only the **final
selected set**, not the full ranked table. Selected results can contain fewer
names than `--top`.

## Results and boundaries

Output is an ignored private `local_data/*.json` file containing
`selected`, `ranked`, `excluded`, the exact factor weights, eligibility
filters, each factor's raw value and percentile, and hard
`live_execution_enabled: false`.

The tool never creates orders, changes portfolio weights, connects to IBKR
or claims a higher return than SPY. Historical market-data adjustments,
today's survivors, inaccurate availability dates, reporting delays,
restatements, illiquid stocks, earnings quality and changes to constituents
can all invalidate naive backtest conclusions. No spread or transaction
costs, borrow costs, liquidity constraints, independent point-in-time data
audit or unseen out-of-sample performance verification is implemented yet.

**Planned, not included:** historical point-in-time fundamentals ingestion,
survivorship-safe universe construction, stock-selection walk-forward
backtesting against SPY and Atlas FusionNet ranking/risk integration.
