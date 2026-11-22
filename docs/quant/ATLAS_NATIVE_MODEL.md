# AtlasPM v0.6.0 — Our OWN trainable AI architecture

**Atlas RegimeNet v1** is the beginning of an internally designed, independently trained risk-and-return architecture. Unlike the earlier research toolkit which simply trained open-source Ridge and LightGBM, this version defines and fits our **own multi-asset network**, objective, training features, portfolio score and rebalancing policy. It uses ordinary NumPy array operations and standard public ML math (softmax, gradients); it makes **no false claim of inventing those mathematical building blocks** or owning proprietary training data.

The old LightGBM, Ridge and historical-volatility models remain available as transparent *benchmarks*, not the core Atlas AI. Nothing was copied from a proprietary or pretrained portfolio model.

## Architecture

Three latent experts are trained jointly from scratch across 2–12 assets:

1. **Past-only market and asset features:** daily return, 5-day momentum, historical mean, 5-day and lookback realized risk, downside risk, cross-asset market mean momentum and mean risk, plus one-hot asset identity. All derived solely from previously observed price returns.
2. **Learned regime gate:** a softmax network computes a probability for each of three *latent* experts. The experts are not explicitly labeled crash/bull/bear until validated.
3. **Learned dual prediction heads:** each expert predicts log future annualized risk (RMS of daily returns), and forward annualized arithmetic return; gating combines the forecasts. This is an original joint **risk + expected-return** training experiment, not a guarantee of an alpha signal.
4. **Our custom training loss:** standardized log-risk loss, standardized forward-return loss at half weight, additional penalty for underestimating risk, and regularization. Custom gradient updates with deterministic initialization, gradient clipping, and retained best observed training parameters.
5. **Our asset allocation score:** expected-return-adjusted inverse-risk preference, constrained by user-defined max concentration. The expected-return head may *reduce* but can never increase exposure above the independent risk maximum.
6. **Independent hard budget checks:** the same existing 0–1x cash-only concentration, annual risk, hypothetical uniform stress, historical daily expected-shortfall, and account drawdown limits as the earlier experiments.

At each historical decision, only labels fully observed by that close can be used to train. The portfolio signal is generated at prior close, trade is simulated next close, and return accrual begins later. There is no broker order connection.

For comparable performance, Atlas RegimeNet is evaluated beside traditional trailing-volatility risk allocation (identical risk constraints and fees) and equal-weight buy-and-hold. Compare its results against v0.5.x model baselines run on the **same** CSV and date/risk/cost settings. Model training loss declining is **not** evidence of superior out-of-sample returns.

## Windows PowerShell: real training from scratch, 2000–2026

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[native]"

# Test learned model, no future leakage, fund/ledger reconciliation, controls:
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_atlas_regimenet*.py" -v

# Train our own native architecture repeatedly and simulate historical rebalancing:
.\.venv\Scripts\python.exe -m atlaspm.cli atlas-native-backtest --file local_data\market_2000_2026.csv --feature-days 20 --forecast-days 5 --min-train 756 --train-window 504 --sample-stride 2 --epochs 40 --rebalance-days 21 --max-exposure 1 --output local_data\atlas_regimenet_2000_2026.json
```

The native model is more involved than a single linear fit because each rebalance trains a dual-head gated network from scratch using expanding history eligibility and a bounded rolling fitting window. Hardware, NumPy/BLAS configuration, history length and number of assets determine processing time; no runtime estimate is promised.

To review your **private** research summary:

```powershell
$atlas = Get-Content .\local_data\atlas_regimenet_2000_2026.json -Raw | ConvertFrom-Json
$atlas.performance | Format-List
$atlas.forecast_validation | Format-List
$atlas.rebalances | Select-Object -Last 2 | Format-List
```

## What is ours vs what is standard

| Component | Status |
|---|---|
| Cross-asset features and joint risk/return targets | Atlas-designed |
| 3-expert gate-and-head combination, cost-sensitive training objective | Atlas-designed implementation using standard public learning methods |
| NumPy numeric arrays | General computing library, not an investment model |
| Ridge, LightGBM, Qlib examples | **Not used in Atlas RegimeNet training**, retained as comparative baselines |
| Initial weights | Seeded random, **trained locally from scratch** |
| Final fitted model | Local JSON parameters and reports under ignored `local_data/`; not validated for actual trading |
| Historical data | Your local CSV, currently Yahoo/yfinance research data, **not proprietary to AtlasPM** |
| Portfolio risk gates | Existing AtlasPM internally implemented hard guardrails, not a broker approval |
| Live trading | **Disabled**, no IBKR order requests or unattended operation |

## Limitations and roadmap

This v1 is an original architecture/prototype, **not** a proven new financial forecasting algorithm or proprietary hedge-fund model. The market-state gate could learn generic latent mixtures rather than economically interpretable regimes; the return head can overfit. The data may be revised and benchmark assets can be non-tradable at ETF execution timings; the simulation ignores market impact, realistic yields on idle cash, dividends not reflected as actual cashflows, taxes, broker margin and fillability.

Before considering paper orders or self-directed investment decisions, build a **genuinely independent locked time split**, e.g. 2000–2015 initial research, 2016–2020 validation, 2021–2026 untouched final evaluation; add regime diagnostics, market microstructure data, cost-aware turnover penalties, calibration and ablation tests, a proper asset benchmark matched for risky exposure, and scenario robustness tests.

At every stage the flags `model_validated_for_live_trading=false`, `IBKR_financing_debt_separately_verified=false`, `ready_for_institutional_risk_gate=false`, and `live_execution_enabled=false` remain false. **No broker paper/live orders, margin borrowing or leveraged autonomous trades are implemented.**


## v0.6.1: Diagnose why native V1 lost to traditional risk control

The first user's 2000–2026 walk-forward experiment showed:
- Atlas native: 198.72% total historical simulated return; Sharpe 0.856; max drawdown -12.52%; turnover 37.37x
- Naive trailing volatility: 215.58%; Sharpe 1.007; max drawdown -12.53%; turnover 31.34x
- Atlas future annualized arithmetic-return MAE was about 0.717, and the 30% uniform shock with 25% max shock loss often limited gross exposure to 0.8333.

This is evidence **against claiming the v1 model has investable alpha**. The model is learning its in-sample dual-head loss, but we must test the return head's *incremental* value versus predicting zero and reduce churn without concealing a risk breach.

### Controlled experiments (same dates, same risks)

The original native v0.6.0 behavior remains available via the explicit defaults `--return-head-strength 1 --no-trade-band 0`. Preserve the previous report as an unchanged comparison before running variants.

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[native]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_atlas_regimenet*.py" -v

# A — full native dual-head model, but lower trading with fresh risk rechecks.
.\.venv\Scripts\python.exe -m atlaspm.cli atlas-native-backtest --file local_data\market_2000_2026.csv --feature-days 20 --forecast-days 5 --min-train 756 --train-window 504 --sample-stride 2 --epochs 40 --rebalance-days 21 --return-head-strength 1 --no-trade-band 0.05 --max-exposure 1 --output local_data\atlas_v061_cost_2000_2026.json

# B — risk-only allocation ablation, keep the same band and same supervised network.
.\.venv\Scripts\python.exe -m atlaspm.cli atlas-native-backtest --file local_data\market_2000_2026.csv --feature-days 20 --forecast-days 5 --min-train 756 --train-window 504 --sample-stride 2 --epochs 40 --rebalance-days 21 --return-head-strength 0 --no-trade-band 0.05 --max-exposure 1 --output local_data\atlas_v061_risk_only_2000_2026.json
```

This ablation disables the **return head's influence on weights and additional risk exposure** but still trains the return head alongside risk. It is *not* a separately retrained risk-only network. Disabling return-head allocation does not guarantee gains; the purpose is to measure whether our current noisy five-day forward-return predictions have any portfolio value.

The no-trade band is an **L1 distance of account asset and cash weights**. E.g. 0.05 means up to 5 percentage points in total absolute weight difference. At every signal, even if inside the band, the actual held risky composition is re-validated against concentration, forecast-volatility, historical expected shortfall, uniform stress, exposure, drawdown and native exposure-reduction caps. A failed check forces a trade to the newly risk-approved target. No skips are allowed on initial entry. The **traditional model is given the exact same no-trade rule** in the new backtest to avoid giving AI special execution advantages.

New diagnostics:
- `forecast_validation.return_mae_annualized` versus `zero_return_baseline_mae_annualized`: whether our learned expected-return estimates beat predicting zero, in the same annualized noisy 5-day target definition
- `forecast_validation.risk_mae_annualized` versus `naive_trailing_risk_mae_annualized`: forecast accuracy relative to traditional risk
- `execution_summary.atlas_native.skipped_rebalance_decisions` and `execution_summary.naive_trailing_vol_risk.skipped_rebalance_decisions`, plus updated equity turnover and fees
- `rebalances[*].native_execution_policy` and `baseline_execution_policy`: each decision's actual position gap, whether skip passed updated risk checks and its reason

```powershell
$a = Get-Content .\local_data\atlas_v061_cost_2000_2026.json -Raw | ConvertFrom-Json
$b = Get-Content .\local_data\atlas_v061_risk_only_2000_2026.json -Raw | ConvertFrom-Json

$a.forecast_validation | Format-List
$a.execution_summary | Format-List
$a.performance | Format-List

$b.execution_summary | Format-List
$b.performance | Format-List
```

All two variants use the exact same historical dates and feature training. Since the 2021–2026 outcomes have already been studied repeatedly, this is *model development*, not an untouched holdout or independent proof of improved performance. We must freeze any final candidate on separate yet-unseen data later. Lower trading costs from skipped trades, if observed, do not guarantee better net total returns or Sharpe.
