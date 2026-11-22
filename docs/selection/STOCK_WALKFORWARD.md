# AtlasPM: 2000–2026 stock-selection walk-forward research

**Status:** experimental price-only, 0–1x cash equity simulation. It is **not
AI training**, not a real brokerage backtest, and not independently validated
alpha. Existing `stock-screen` remains unchanged. This is a separate
`stock-backtest` command.

## Windows PowerShell: run against your local 26-year files

You have `local_data/us_stocks_2000_2026.csv` and previously downloaded
`local_data/market_2000_2026.csv`. The latter should contain SPY's adjusted
closes. Everything stays under gitignored `local_data/`.

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git fetch origin
git switch --track origin/feature/stock-walkforward-v1
.\.venv\Scripts\python.exe -m pip install -e .

# First: verify offline unit tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_stock*.py" -v

# Monthly (~21 trading sessions) walk-forward using only price records available
# on each decision date; next-close execution and transaction-cost assumptions:
.\.venv\Scripts\python.exe -m atlaspm.cli stock-backtest --prices local_data\us_stocks_2000_2026.csv --benchmark-prices local_data\market_2000_2026.csv --benchmark-symbol SPY --start 2000-01-01 --end 2026-11-20 --top 10 --rebalance-days 21 --fee-bps 5 --slippage-bps 5 --max-volatility 0.80 --max-drawdown 0.65 --output local_data\stock_walkforward_2000_2026.json

# Compact summary: no private ticker history printed
$r = Get-Content .\local_data\stock_walkforward_2000_2026.json -Raw | ConvertFrom-Json
$r.actual_window
$r.portfolio | Format-List
$r.benchmark | Format-List
$r.relative | Format-List
$r.yearly_returns | Format-Table year,sessions,portfolio_return,spy_return,excess_percentage_points
$r.executions | Select-Object -Last 3 decision_date,execution_date,selected
```

If `market_2000_2026.csv` lacks SPY, obtain its history through the existing
`download-prices` command:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[market]"
.\.venv\Scripts\python.exe -m atlaspm.cli download-prices --symbols SPY --start 2000-01-01 --end 2026-11-21 --output local_data\spy_2000_2026.csv
# Replace --benchmark-prices with local_data\spy_2000_2026.csv
```

The download start is inclusive, end **exclusive**. Vendor data are unofficial
and may fail or be revised. Do not upload `local_data/` or broker account
data to GitHub.

## Matched-cost full-universe comparison (new)

Each completed decision close also constructs a **separate** 1× equal-weight
portfolio of **all supplied stocks with valid 253-close history** on that day,
including those rejected by the factor strategy's volatility/drawdown gate.
Membership never depends on the top-N factor rank. For every scheduled decision:

- The strategy and universe comparison both use the same completed decision
  date, following-session close for hypothetical execution, 21/42/63-session
  frequency, per-dollar fee/slippage, 0% cash interest and no borrowing.
- Newly listed stocks become eligible once their 253-close history is
  sufficient. The comparison does not backfill them to 2000.
- Missing future execution or held price for *either* strategy triggers an
  error, not a fictional mark or silent exclusion.
- Outputs add `equal_weight_universe` CAGR, cumulative return, zero-risk-free
  Sharpe, volatility, drawdown, its execution costs, daily
  `universe_index`, annual `universe_return`, and
  `relative.cagr_minus_equal_weight_universe_percentage_points`.

To compare with the new in-file benchmark, rerun the identical backtest:

```powershell
git fetch origin
git switch --track origin/feature/stock-universe-benchmark-v1
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_stock*.py" -v
.\.venv\Scripts\python.exe -m atlaspm.cli stock-backtest --prices local_data\us_stocks_2000_2026.csv --benchmark-prices local_data\market_2000_2026.csv --start 2000-01-01 --end 2026-11-20 --top 10 --rebalance-days 21 --fee-bps 5 --slippage-bps 5 --output local_data\stock_with_equal_weight.json
$r = Get-Content local_data\stock_with_equal_weight.json -Raw | ConvertFrom-Json
[PSCustomObject]@{
  Start = $r.actual_window.start
  End = $r.actual_window.end
  FactorCAGR = [math]::Round($r.portfolio.cagr * 100, 2)
  UniverseCAGR = [math]::Round($r.equal_weight_universe.cagr * 100, 2)
  SPYCAGR = [math]::Round($r.benchmark.cagr * 100, 2)
  FactorMinusUniversePP = [math]::Round($r.relative.cagr_minus_equal_weight_universe_percentage_points, 2)
} | Format-List
```

**Interpretation:** The comparison tests factor selection/risk filtering
**relative to this same surviving-ticker universe**, not whether the selected
universe is representative of everything investable in past decades. Both
strategies share substantial survivor, availability and corporate-action
data biases. Also this is **periodic rebalanced equal-weight**, NOT a
one-time buy-and-hold benchmark.

## Methodology: avoid same-close P&L lookahead

1. **Warm up 253 closes** on SPY's trading calendar; the first decision is
   typically in 2001 when prices begin in 2000. The first actual evaluated
   date is reported, not falsely labeled January 2000.
2. On the decision session's *completed close*, compute each company's
   `stock-screen` technical-factor rank using no dates later than the
   decision close. This uses 12–1/6–1 momentum, 200-day SMA, last-63-return
   volatility and trailing 253-close drawdown. Default volatility cap 80%,
   drawdown cap −65%, and selection Top 10. A company must have a price **on
   the decision date**.
3. Schedule equal-weight Top N allocation for the **next** benchmark-session
   close. No selection depends on the future execution price. If that future
   close is absent, abort instead of back-filling/forward-filling.
4. Accrue mark-to-market returns on existing shares through the next close,
   then hypothetically rebalance at that close. Only subsequent close-to-close
   changes affect newly bought shares. Charge `(fee_bps+slippage_bps)/10000`
   per dollar traded, including a new initial position and subsequent
   sales/purchases. One-way dollar turnover can exceed 1 when selling and
   buying a different full basket.
5. SPY is a standalone buy-and-hold *adjusted-close* benchmark starting at
   the identical first decision close. There are **no SPY benchmark fees**.
   Assume uninvested cash earns 0%; no margin, shorts, borrowing or leverage.
6. Output full per-day normalized equity curves, allocation decisions,
   execution turnover/costs, portfolio & SPY cumulative return, CAGR,
   zero-risk-free Sharpe, volatility, maximum drawdown and per-year returns.
   CAGR annualizes over 252 observed benchmark sessions per year. Yearly
   values may cover only part of a calendar year.

### Critical research caveats

- The supplied symbols represent a **present-day survivor-biased universe**.
  The engine does **not** automatically include delisted, bankrupt or merged
  stocks, or reconstruct historical index membership. It is unsuitable for
  making validated institutional 2000–2026 alpha claims.
- A company's first public trading date naturally limits availability; firms
  that did not exist in 2000 are not falsely given prices.
- Yahoo-style adjusted historical prices are revised retrospectively; this
  module cannot certify data vintages or pre-trade data availability.
- The present implementation has **no fundamental factors**, historical
  company-financial publication archive, transaction volume/spread caps,
  market-impact or tax modeling. Fee/slippage are approximations.
- A missing held or newly selected share price on a required date causes an
  explicit failure, rather than inventing a return. Existing history may
  require a point-in-time data-quality audit before the experiment runs end
  to end.
- All earlier 2000–2026 research and this user-selected sample have already
  influenced development. They are **not untouched out-of-sample data**.
- An annualized historical gain, Sharpe or drawdown is **descriptive**, not a
  forecast, proof of causality or proof the strategy works live. Validate on
  independently sourced, surviving-and-delisted constituent records first.

## Planned (not yet present)

A full historical eligible-universe archive, independent survivorship-safe
data provider, point-in-time fundamentals, genuine walk-forward selection of
trained ranking models, separate unseen test segments, risk budgeting
integration with Atlas FusionNet, and uncertainty/cost/sector stress audits.
