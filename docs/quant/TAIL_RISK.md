# Rolling historical Expected Shortfall (ES / CVaR) research budget

AtlasPM v0.3.1 adds an **optional, strictly past-only empirical one-day Expected Shortfall** budget to the adaptive risk backtester. The base 0.3.0 behavior is unchanged unless you pass `--max-daily-tail-loss`.

## Windows PowerShell example

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli backtest-risk --file local_data\prices.csv --lookback-days 756 --rebalance-days 63 --risk-aversion 4 --max-weight 0.60 --max-leverage 2 --max-volatility 0.30 --stress-decline 0.30 --max-stress-loss 0.40 --maintenance 0.30 --min-buffer 0.05 --apr 0.065 --max-financing-cost 0.08 --fee-bps 5 --max-daily-tail-loss 0.03 --tail-fraction 0.05 --output local_data\backtest_tail_3pct.json

.\.venv\Scripts\python.exe -m atlaspm.cli benchmark-spy --cash local_data\backtest_cash.json --margin local_data\backtest_tail_3pct.json --spy local_data\spy_prices.csv --output local_data\spy_comparison_tail_3pct.json
```

- `--tail-fraction 0.05`: worst 5% of *past daily risky-sleeve returns*. For a lookback window of 756 trading days, take the worst `ceil(756 × 0.05) = 38` observations.
- `--max-daily-tail-loss 0.03`: at the time of each scheduled rebalance, the **historically estimated average loss over those worst days** after applying gross leverage is limited to 3% of current account equity. This is **not** a worst-day maximum and **not** a 3% maximum drawdown guarantee.
- The empirical estimator uses the daily aligned returns ending on the **signal day** (one session before hypothetical execution). It never reads future session returns.
- Estimated unlevered tail risk is based on the *risky-sleeve weight mix*; permitted exposure is the minimum of the current upper bounds for requested leverage, modeled annual volatility, 30% uniform crash stress, illustrative maintenance cushion, annual financing cost, and optional daily ES.
- Exposure can fall below 1×, with the remainder in cash earning 0%. The research backtest currently rebalances every 63 trading days by default and **does not react between rebalances**.
- Detailed `historical_daily_tail_risk`, `historical_daily_es_at_selected_exposure`, and `bounds.historical_daily_expected_shortfall` appear in each rebalance decision and the full local JSON report. The terminal summary includes an `tail_risk_binding_rebalance_count` diagnostic.

## Caveats

**Historical Expected Shortfall (ES) is not a prediction of future crash behavior.** It only averages losses in the worst observed portion of a training window. Data before 2000, changes in correlation, intraday gaps, execution costs during crashes, taxes, real broker financing and house margin are not modeled. Prior-period tail losses may be much smaller than realized future tail losses.

This is not a directly optimized CVaR portfolio: current allocation composition remains a long-only mean–variance solution; the ES constraint scales the **overall risky exposure**. A future allocation optimizer should directly adjust constituent weights under tail scenarios, rather than only scale the existing weights.

The dataset and parameter choices were developed after examining 2003–2026 results. Although each simulated rebalance uses past returns, the full study is **not a blind holdout** because of strategy-design and universe-selection bias. Do not treat improved historical performance as proof of alpha or broker approval. No live broker connections or orders are available.
