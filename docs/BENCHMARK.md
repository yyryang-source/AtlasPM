# Independent SPY benchmark (research only)

The existing AtlasPM backtest uses an **equal-weight buy-and-hold of the optimized universe**, which contains a hindsight-selected set of stocks. This extension adds a separate **SPY adjusted-close** buy-and-hold reference, **without** adding SPY to the optimizer's universe.

## Windows PowerShell

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli download-prices --symbols SPY --start 2000-01-01 --end 2026-11-23 --output local_data\spy_prices.csv

.\.venv\Scripts\python.exe -m atlaspm.cli benchmark-spy --cash local_data\backtest_cash.json --margin local_data\backtest_margin_2x.json --spy local_data\spy_prices.csv --output local_data\spy_comparison.json
```

For an alternate simulated margin-deleveraging case, replace `--margin` with `local_data\backtest_margin_forced.json` and choose a different output path.

## Comparison methodology

- From saved cash/margin JSON files, verify identical strategy dates, parameters and symbols via the existing risk-audit validator.
- Read only a **separately downloaded** SPY-adjusted-close CSV.
- Buy hypothetical SPY at the **first simulated strategy execution close**; hold throughout the identical strategy daily dates.
- Require a SPY adjusted-close observation for **every** simulated return date. Missing dates cause a clear error rather than a forward-fill.
- Report its compounded CAGR, volatility, max drawdown, zero-risk-free-rate Sharpe approximation, and excess strategy CAGR relative to SPY.

There is no commission or slippage, and adjusted-close data comes from Yahoo Finance via unofficial yfinance. SPY is an ETF proxy for the S&P 500, not a perfectly matched risk benchmark (especially versus leverage or concentrated tech positions).

**Warning:** the user's strategy still has hindsight stock-universe selection and provider-data limitations, and SPY's performance should not be used to claim verified alpha.
