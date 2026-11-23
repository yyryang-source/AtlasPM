# AtlasPM: 2000–2026 stock-selection walk-forward research

`stock-backtest` runs an experimental 0–1x cash equity simulation with the
technical or combined fundamental and technical factors from `stock-screen`.

## Windows PowerShell: run against your local 26-year files

You have `local_data/us_stocks_2000_2026.csv` and previously downloaded
`local_data/market_2000_2026.csv`. The latter should contain SPY's adjusted
closes. Everything stays under gitignored `local_data/`.

```powershell
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

## Supply historical membership

Pass `--universe-history local_data\historical_members.csv` to use the same
historical membership in the strategy and the matched-cost equal-weight
baseline. Each snapshot lists **every member** on a date when that membership
was effective and known by the trading close. The dates and symbols below are
illustrative.

```csv
as_of_date,symbol
2024-01-31,EXAMPLE_A
2024-01-31,EXAMPLE_B
2024-02-29,EXAMPLE_B
2024-02-29,EXAMPLE_C
```

At each decision, the engine selects the most recent snapshot dated on or
before the decision close. It requires a snapshot no more than 35 calendar
days old by default. Use `--max-universe-age-days 0` for same-day snapshots.
Duplicate symbols within a snapshot, missing or stale snapshots, and missing
decision-day closes for any member stop the run. A member with fewer than 253
closes can enter the strategy and baseline once it meets the warmup and other
price eligibility rules. Reports record the snapshot date and member count for
each decision and the number of distinct members used across decisions.

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli stock-backtest --prices local_data\us_stocks_2000_2026.csv --benchmark-prices local_data\market_2000_2026.csv --universe-history local_data\historical_members.csv --output local_data\stock_historical_universe.json
```

Supply closes through each simulated exit or supply the documented cash
delisting events below. The quality of a historical run depends on the
completeness and timing of the provided membership data.

### Audit the inputs before running

`stock-history-audit` checks every scheduled decision using the same benchmark
calendar, date window, rebalance interval and snapshot freshness rules as
`stock-backtest`. The report lists missing member closes and unusable snapshots
by date. Coverage is calculated over decisions with usable snapshots, with
the number of other decisions reported separately. Review both fields before
running the backtest. Held-position quotes and execution prices are checked
during the backtest itself.

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli stock-history-audit --prices local_data\us_stocks_2000_2026.csv --benchmark-prices local_data\market_2000_2026.csv --universe-history local_data\historical_members.csv --start 2000-01-01 --end 2026-11-20
$audit = Get-Content local_data\stock_history_audit.json -Raw | ConvertFrom-Json
$audit.decisions | Where-Object status -ne COMPLETE
```

### Account for cash delistings

Pass `--delistings local_data\delistings.csv` for cash-only terminal events.
Use a stable security identifier across prices, membership, and events when a
ticker was reused. These values are illustrative.

```csv
symbol,last_trade_date,cash_date,return_from_last_close
EXAMPLE_A,2024-04-11,2024-04-29,-0.50
EXAMPLE_B,2024-06-03,2024-06-14,-1.00
```

Use the documented final trading close and a later date on which the net cash
is available or the loss is confirmed. The return must be expressed relative to
the final adjusted close, with `-1.00` for a confirmed total loss. Normalize
vendor missing-value codes and avoid counting a terminal return already
embedded in the price series. Check the cash basis of each event because data
providers can also quote off-exchange values or stock consideration.

After the final trading session, an open position becomes a receivable carried
at the last close. The amount cannot fund a rebalance. The engine applies the
verified cash value after the first modeled close on or after `cash_date`, so
the proceeds are usable at the next session. A selected stock that delists
between the decision and the execution stays in the decision record, and its
unfilled allocation stays in cash. Both portfolios apply the same rules and
report settlements and unsettled balances. Use historical snapshots that
remove the security from subsequent decisions. An unexplained missing close
continues to stop the run.

Market data inputs for stock consideration, multiple payouts, and intraday
events require more detailed event modeling. The current single-payout format
is informed by [CRSP's delisting return convention](https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-data-descriptions-guide-crspaccess/)
and [LEAN's treatment of delisting as a separate event](https://www.quantconnect.com/docs/v2/writing-algorithms/securities/asset-classes/us-equity/corporate-actions).

## Include publication-dated fundamentals

Use the [stock-screen fundamentals format](STOCK_SCREENER.md#inputs). For each
decision session, the rank uses the latest snapshot available by that close.
Missing or stale snapshots exclude the stock from the strategy. The price-based
equal-weight benchmark retains its own universe and uses the same decision
dates and cost assumptions.

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli stock-backtest --prices local_data\us_stocks_2000_2026.csv --benchmark-prices local_data\market_2000_2026.csv --fundamentals local_data\published_fundamentals.csv --max-fundamental-age-days 200 --min-market-cap 10000000000 --max-per-sector 2 --output local_data\stock_fundamental_walkforward.json
```

The report records the ranking mode, filter settings, and the publication date
used for each selection. For after-close releases, use the next decision-session
date in the input. Supply the as-published values from each period and preserve
the publication date of any later restatement.

## Compare four strategies on a fixed test window

`stock-compare` uses one cohort per decision: historical members with eligible
price history, the standard risk filters and available published fundamentals.
The equal-weight baseline invests across that cohort. Technical factors,
technical plus fundamentals, and LightGBM each rank the same cohort and select
up to the same Top N, using equal sizing within each resulting selection.
When a group of tied scores straddles the cutoff, the portfolio uses only the
groups above the tie. Each portfolio uses the same next-close simulation,
fees, slippage and optional cash delisting events. The original `stock-backtest`
continues to implement its existing broader price-based baseline.

Choose the train, validation and final test boundaries before reviewing results.
LightGBM fits on train decisions and chooses the stopping iteration on validation
decisions. The forward label runs from the next close for one rebalance interval.
The comparison purges labels that cross a split boundary and drops entire
training or validation dates with incomplete cohort outcomes. Test decisions use
the frozen model. The report includes input SHA-256 hashes, period settings,
model details, purged and incomplete cohort counts, net returns, drawdown, costs
and turnover. See the [Qlib workflow](https://qlib.readthedocs.io/en/latest/component/workflow.html)
and [LightGBM grouping reference](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRanker.html).

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[ml]"
.\.venv\Scripts\python.exe -m atlaspm.cli stock-compare --prices local_data\stock_prices.csv --benchmark-prices local_data\benchmark.csv --universe-history local_data\historical_members.csv --fundamentals local_data\published_fundamentals.csv --delistings local_data\delistings.csv --train-start 2014-01-01 --train-end 2018-12-31 --validation-end 2020-12-31 --test-start 2021-01-01 --test-end 2025-12-31 --output local_data\stock_comparison.json
```

The dates illustrate the command. Use windows covered by your inputs, with
at least 253 benchmark closes before the first labels and enough complete
decision cohorts for both fitting periods. Report results for a genuinely
unseen test period and document any earlier research on those dates.

## Compare Quant weighting with the same equal-weight picks

Pass `--quant` to simulate an additional portfolio using the exact same picks
and dates as the ranked equal-weight strategy. The optimizer estimates a
trailing covariance matrix through the decision close, with 25% shrinkage
toward its diagonal. At the next close, it uses current holdings and execution
prices to size orders. The decision-time ranks and covariance stay fixed.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[portfolio]"
.\.venv\Scripts\python.exe -m atlaspm.cli stock-backtest --prices local_data\stock_prices.csv --benchmark-prices local_data\benchmark.csv --start 2016-01-01 --end 2020-12-31 --top 2 --quant --quant-max-weight 0.55 --quant-turnover-limit 1.25 --output local_data\stock_quant_comparison.json
```

The objective balances covariance risk, a preference for invested capital
scaled by 0.20 per year, the factor-score tilt, and the fee and slippage on the
trade. The exposure preference and score tilt are modeling coefficients.
The long-only optimization can hold cash. The stock cap and optional sector
cap use post-cost liquid equity. Set `--quant-sector-cap` with
`--fundamentals` to use sectors known at each decision. Turnover is the sum of
absolute buy and sell notionals divided by pre-trade liquid equity, so a full
rotation can approach 2. Both the Quant and equal-weight portfolios apply the
same cost rate to executed notionals in this mode.

If required exits or a concentration limit conflict with the turnover cap,
the Quant sleeve skips the rebalance and records the reason. Retained weights
can be above a cap after price drift. Insufficient exact-session covariance
history also produces a skip. Review `quant.executions` for the decisions,
binding limits, before/after weights, turnover, cash, and cost. Compare
`quant.portfolio` with `portfolio`, the equal-weight result for the same picks.
The approach follows the single-period formulation described by
[Boyd et al.](https://web.stanford.edu/~boyd/papers/cvx_portfolio.html) and the
[Cvxportfolio constraint model](https://www.cvxportfolio.com/en/stable/constraints.html).

## Matched-cost full-universe comparison (new)

Each completed decision close also constructs a **separate** 1× equal-weight
portfolio of **all supplied stocks with valid 253-close history** on that day,
including those rejected by the factor strategy's volatility/drawdown gate.
With historical snapshots enabled, this set starts from the historical members
for the same decision. For every scheduled decision:

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

The comparison measures selection and risk filtering within the same supplied
universe. Both strategies share any availability, survivorship and
corporate-action biases in the input. The equal-weight benchmark rebalances
on the same schedule as the strategy.

## Methodology: avoid same-close P&L lookahead

1. **Warm up 253 closes** on SPY's trading calendar; the first decision is
   typically in 2001 when prices begin in 2000. The first actual evaluated
   date is reported, not falsely labeled January 2000.
2. On the decision session's *completed close*, compute each company's
   `stock-screen` factor rank using data available through the decision close.
   Technical factors include 12–1/6–1 momentum, 200-day SMA, last-63-return
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

- A current listing supply introduces **survivorship bias**. Provide historical
  snapshots containing the stocks that subsequently delisted, went bankrupt or
  merged, plus price coverage through simulated exits.
- A company's first public trading date naturally limits availability; firms
  that did not exist in 2000 are not falsely given prices.
- Yahoo-style adjusted historical prices are revised retrospectively; this
  module cannot certify data vintages or pre-trade data availability.
- The caller supplies the historical company-financial publication archive.
  Fee/slippage are approximations. Transaction volume, spreads, market impact
  and taxes are outside the model.
- A missing held or newly selected close requires a documented cash delisting
  event or a price correction before the backtest can continue.
- All earlier 2000–2026 research and this user-selected sample have already
  influenced development. They are **not untouched out-of-sample data**.
- An annualized historical gain, Sharpe or drawdown is **descriptive**, not a
  forecast, proof of causality or proof the strategy works live. Validate on
  independently sourced, surviving-and-delisted constituent records first.

## Planned (not yet present)

A vetted historical eligible-universe archive, independently verified
fundamentals, genuine walk-forward selection of
trained ranking models, separate unseen test segments, risk budgeting
integration with Atlas FusionNet, and uncertainty/cost/sector stress audits.
