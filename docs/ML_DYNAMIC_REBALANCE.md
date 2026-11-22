# AtlasPM v0.5.0–v0.5.1 — Trained ML Dynamic Portfolio Rebalancing Engine

**v0.5.1 Windows correction:** The `written` path in redacted ML CLI output is now always relative to the project's `local_data/` directory, never an absolute Windows profile path. A previous cross-platform test incorrectly searched JSON-escaped Windows path strings instead of parsing the JSON and asserting the normalized output path. CI now runs this regression on Windows and Linux. The model itself and offline-only safety flags are unchanged.

**Research-only.** AtlasPM now fits a real, supervised machine-learning model and uses its forecasts to automatically set asset weights and cash levels during a strictly out-of-sample historical simulation. This is a first technical training baseline, not a profitable or validated AI trading strategy. **It never submits any IBKR real or paper orders.**

The trained model is a **regularized ridge linear regression** for each asset, predicting the log of the next 5-day annualized *root mean square* of daily adjusted-price returns (an absolute-risk proxy). It is genuine supervised fitted machine learning with learned coefficients, not a hardcoded buy/sell rule; it does **not** predict asset returns, alpha, or the probability of profitable trades. More complex gradient boosted trees and regime-specific risk models can be validated against this transparent baseline later.

## PowerShell: first full training & autonomous simulated rebalancing

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ml_dynamic_rebalance*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# Synthetic 2-asset research data, NOT actual market prices or broker holdings.
.\.venv\Scripts\python.exe -m atlaspm.cli ml-rebalance-backtest --file examples\synthetic_prices.csv --feature-days 20 --forecast-days 5 --min-train 30 --rebalance-days 10 --target-volatility 0.18 --max-exposure 1 --output local_data\ml_rebalance_backtest.json
```

The console prints only the number of assets, fitted training samples, rebalance decisions, held-out sessions, and whether forecast MAE was smaller than an **untrained trailing-volatility benchmark**. It deliberately does **not** print detailed assets, learned coefficients, account amounts or full strategy performance; these remain in `local_data/`. Do not commit this folder.

To inspect the **private** performance/rebalancing report on your own Windows machine:

```powershell
$report = Get-Content .\local_data\ml_rebalance_backtest.json -Raw | ConvertFrom-Json
$report.performance | Format-List
$report.forecast_validation | Format-List
$report.rebalances | Select-Object -Last 3 | Format-List
```

If your price CSV contains real or personal portfolio data, keep even that input under `local_data/`.

## Real market research (optional; NOT brokerage execution)

Existing AtlasPM has an optional unofficial adjusted-price downloader (requires `pip install -e '.[market]'`). You may use a locally imported, properly licensed adjusted-close CSV with columns `date,symbol,adjusted_close` (one row for each asset and date). Different symbols must have a sufficient intersection of price dates. Example historical **ETF proxies**, not cash Treasuries:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[market]"
.\.venv\Scripts\python.exe -m atlaspm.cli download-prices --symbols SPY QQQ IEF --start 2018-01-01 --end 2026-11-01 --output local_data\market_research_prices.csv
.\.venv\Scripts\python.exe -m atlaspm.cli ml-rebalance-backtest --file local_data\market_research_prices.csv --min-train 120 --rebalance-days 10 --output local_data\ml_market_research.json
```

Internet download requires connection and provider availability. The date range above is an example, not evidence that prices were downloaded or correctly adjusted. **IE​​F ETF is not a directly held Treasury bond**; it has market-price and duration risk. The downloader does not verify historical point-in-time constituents, corporate actions, or real transaction prices.

## How AI determines hypothetical rebalances

For each asset and historical decision date:

1. Compute **past-only** daily-return features: last return, five-day mean, five-day RMS, 20-day RMS, and downside RMS. The lookback window is configurable.
2. Train ridge regression on **fully observed historical forward-horizon labels**. The most recent eligible training sample must end by the signal date. Models are refit using an expanding history every rebalance period.
3. Predict each asset's future *risk* and assign more weight to relatively lower predicted-volatility assets with a strict per-name cap.
4. Combine these weights with hard risk policy limits: target annual risk, maximum account gross exposure (at most **1x**), market shock budget, observed historical daily ES cap and equity drawdown guard. Excess notional is held as cash.
5. Observe signals at close `t-1`, simulate rebalance at close `t`, and realize changes `t→t+1`. Add per-traded-notional fee and slippage assumptions. **No broker orders are created.**

This is automatically driven by learned model outputs each rebalance, not a human-entered target allocation. Still, nothing can place real trades, and the model has not been validated to predict price direction or achieve positive alpha.

### Evaluation

A single saved result contains:

- Three complete comparison equity curves: **trained AI**, an identically budgeted **untrained past-volatility model**, and **equal-weight buy-and-hold**.
- For each: cumulative/annualized simulated return, maximum drawdown, volatility, zero-risk-free Sharpe, cumulative turnover and assumed fees/slippage.
- Held-out forecast MAE against realized future annualized return RMS, compared with a past-volatility baseline. This is *forecast-risks-only* evidence, not direct evidence of superior portfolio returns.
- Rebalance ledger with actual learned model forecasts, selected cash/asset weights, binding constraints, training sample count, fit timestamps and simulated execution timestamps.
- A snapshot of the final trained model's learned coefficients and scaling parameters. These are a research artifact; no inference API or persistent live model deployment has been enabled.

Crucial: **Even an AI forecast beating the naive baseline in one backtest does not validate it for live trading.** Walk-forward periods are overlapping, the number of independent market regimes may be small, transaction-cost model is simplified, training data can be revised, and selection/survivorship bias remains. Validated, independently sourced out-of-sample data and paper trading are future requirements.

### Hard-coded boundaries

The full JSON and redacted stdout permanently retain:

```json
{
  "model_validated_for_live_trading": false,
  "IBKR_financing_debt_separately_verified": false,
  "ready_for_institutional_risk_gate": false,
  "live_execution_enabled": false
}
```

This first version is **long-only 0–1x cash-account allocation**, not a Portfolio Margin or options engine. To create safe *real* autonomous IBKR rebalancing in a later product stage, the system would need authenticated position/funding truth, signed risk policy, independent hard-limit service, order-bound margin What-If, rate limits and cancel/kill switches, reconciliation after execution, paper-trading performance, and separate explicit user activation. None is present now.

Related existing baselines: [Backtest](BACKTEST.md), [adaptive cash and leverage risk backtest](ADAPTIVE_RISK.md), [risk budget](RISK_BUDGET.md).
