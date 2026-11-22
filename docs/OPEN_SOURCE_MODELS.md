# AtlasPM v0.5.2 — Open-source Institutional Research Model Training

## Purpose and what has genuinely been implemented

AtlasPM now supports a **real, native LightGBM supervised regression model** for future annualized *risk*, fitted only from historical adjusted-close returns. LightGBM is the open-source, MIT-licensed gradient-boosted tree algorithm also included in Microsoft Qlib's research benchmarks. **AtlasPM does not include or claim to be an official Qlib/Alpha158 data pipeline, proprietary investment-bank model, or downloaded trained institutional model.**

The optional `--model lightgbm` option trains one model per risky asset at every eligible rebalance with expanding **past-only** fitting windows. Its risk predictions adjust the weights among assets and their total account exposure versus cash. Portfolio accounting, historical-ES/stress/drawdown limits, assumed fees and slippage, and time-safe next-close execution rules are identical to the original Ridge option. The model is compared with the untrained trailing-volatility allocator and equal-weight buy-and-hold; no in-sample backtest results are used to claim superior live trading performance.

### Windows PowerShell installation and independent training

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main

# Install the optional open-source native ML dependency in the existing venv.
.\.venv\Scripts\python.exe -m pip install -e ".[ml]"

# Test the real LightGBM tree training and whole automatic-rebalancing pipeline.
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ml_lightgbm*.py" -v

# Model A: existing simple Ridge ML, on exactly the same fictional prices.
.\.venv\Scripts\python.exe -m atlaspm.cli ml-rebalance-backtest --model ridge --file examples\synthetic_prices.csv --min-train 30 --rebalance-days 10 --output local_data\model_ridge.json

# Model B: real LightGBM trained on those SAME fictional prices.
.\.venv\Scripts\python.exe -m atlaspm.cli ml-rebalance-backtest --model lightgbm --file examples\synthetic_prices.csv --min-train 30 --rebalance-days 10 --output local_data\model_lightgbm.json
```

Compare the *private* full research results without uploading them:

```powershell
$ridge = Get-Content .\local_data\model_ridge.json -Raw | ConvertFrom-Json
$lightgbm = Get-Content .\local_data\model_lightgbm.json -Raw | ConvertFrom-Json

$ridge.forecast_validation | Format-List
$lightgbm.forecast_validation | Format-List
$ridge.performance | Format-List
$lightgbm.performance | Format-List
```

The output includes real trained model tree dumps under `final_trained_model_snapshot[SYMBOL].trained_native_model_text_private`, with no pickle files or automatic execution. It remains entirely private under Git-ignored `local_data/`.

### Real historical data (separate experiment, optional)

The existing optional market-data downloader can request public adjusted ETF price histories for offline experiments. This is **not official Qlib data**, a validated point-in-time dataset or actual broker position prices. Example ETFs `SPY, QQQ, IEF` are only research proxies, including for bond exposure; **IEF is not a direct Treasury bond position**.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[market,ml]"
.\.venv\Scripts\python.exe -m atlaspm.cli download-prices --symbols SPY QQQ IEF --start 2017-01-01 --end 2026-11-01 --output local_data\institutional_research_prices.csv
.\.venv\Scripts\python.exe -m atlaspm.cli ml-rebalance-backtest --model lightgbm --file local_data\institutional_research_prices.csv --feature-days 20 --forecast-days 5 --min-train 180 --rebalance-days 20 --max-exposure 1 --output local_data\institutional_lightgbm_research.json
```

This optional downloader requires a working connection and available Yahoo Finance data. No data are fetched during CI. Downloaded adjusted prices may be revised and can exhibit survivorship or selection bias. Real-world market validations require independent data-provider/source and historical-availability checks.

## Public research and institutional-style sources

| Source | Model or useful content | Use in AtlasPM | Availability and license |
| --- | --- | --- | --- |
| [Microsoft Qlib](https://github.com/microsoft/qlib) | LightGBM, XGBoost, LSTM, Transformer, Alpha158/Alpha360 research workflows | **Model-family methodology reference**, optional upstream research pathway | Public MIT source code; not their proprietary trained institutional weights |
| [LightGBM](https://github.com/lightgbm-org/LightGBM) | Native gradient boosted trees | **Actually trained by AtlasPM v0.5.2** | MIT, optional dependency, fitted from the user's local history |
| [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) | CVaR, EVaR, drawdown, HRP and other portfolio optimizers | Future external optimizer / risk-budget comparison | BSD-3-Clause, not a pretrained AI |
| [PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt) | Black-Litterman, HRP, shrinkage risk models | Future stable comparison optimizer | MIT, not a pretrained AI |
| [AI4Finance FinRL-X](https://github.com/AI4Finance-Foundation/FinRL-Trading) | Modular ML / RL research and portfolio weights | **Future isolated simulation**, not direct trading integration | Apache-2.0; contains live-trade components **not** enabled in AtlasPM |
| [AI4Finance FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | Public financial text model code and selected published weights | Possible later news sentiment features, not autonomous investment decisions | Source MIT; **individual model/data weight licenses must be checked** |
| [AQR Capital Management research datasets](https://www.aqr.com/insights/datasets) | Published value, momentum, quality and defensive factors | Research feature sources, licensing/attribution review needed | Public research data; not proprietary trading signals or current bank positions |
| [Open Source Risk Engine](https://github.com/OpenSourceRisk/Engine) | Pricing, XVA, risk and instrument analytics | Possible institutional fixed-income/derivatives verification | Standalone engineering framework, not an AI prediction model |

Model selection should be empirical, not based on institutional branding. A lower prediction MAE on a tiny synthetic dataset does not prove a lower true drawdown or better future trading returns. After v0.5.2, evaluate separate calendar regimes, stronger sample-size minimums, rolling retraining, trading-cost sensitivity, out-of-sample drawdown/ES and comparable no-AI baselines.

### Native Microsoft Qlib, distinct from this AtlasPM adapter

For reproducing the *official Qlib pipeline*, use Microsoft's repository and its specific supported environment (WSL2/Docker may be convenient on Windows) and follow the corresponding `LightGBM/workflow_config_lightgbm_Alpha158.yaml` configuration and official data preparation. That is a **separate experiment** with Qlib data requirements, universe and target definitions; a Qlib return-prediction result cannot be directly substituted into AtlasPM's future-volatility regression without designing and validating an explicit adapter. Do **not** fetch arbitrary pickle checkpoints from untrusted sources or enable broker API credentials while trying the official research examples.

## Non-negotiable safety boundaries

- Two options (`ridge` and `lightgbm`) only **simulate automatic rebalance** on historical prices. They never send orders or operate IBKR paper/live connections.
- All portfolios stay in cash-account mode, **long-only at or below 1x**. There is no ability to borrow funds or bypass hard risk checks.
- The new LightGBM dependencies are **optional**; users who only need Ridge or risk audit require no extra installation.
- Model coefficients/tree dump, exact holdings, valuations, scenario results and input market history stay in `local_data/`; CLI stdout is redacted.
- `is_pretrained_institutional_model=false`, `is_official_qlib_pipeline=false`, `model_validated_for_live_trading=false`, `ready_for_institutional_risk_gate=false`, `live_execution_enabled=false` remain explicit.
- Even positive out-of-sample performance on one study is not a deployability decision. AI-generated allocations should first be benchmarked and stress-tested across regimes.

This version does not copy vendor source code, does not repackage any proprietary model or protected data and does not claim institutional sponsorship or an investment bank endorsement.
