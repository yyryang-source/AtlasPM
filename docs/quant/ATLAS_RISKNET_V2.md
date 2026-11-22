# AtlasPM v0.6.2 — Atlas RiskNet v2 (OUR risk-only AI)

AtlasPM's **original, scratch-trained** three-expert architecture now has two genuinely different fitted model families:

| Research mode | Fit targets | Training objective | Portfolio inputs |
| --- | --- | --- | --- |
| `--architecture dual --return-head-strength 1` | Forward risk and forward return | Shared dual-output quadratic loss | Risk and uncertain forward-return signals |
| `--architecture dual --return-head-strength 0` | **Still** forward risk and return | Same shared dual-output quadratic loss | Risk only at allocation |
| **`--architecture risknet --return-head-strength 0`** | **Only future log RMS risk** | **Independently fitted asymmetric Huber loss** | Risk-only; there is NO trained return signal |

The third row is a new **independently trained risk network**, not a wrapper for LightGBM/Ridge/Qlib and not a pretrained bank model. It reuses the AtlasPM historical feature engineering and 0–1x cash-account risk/cost ledger for comparable evaluation, but has its **own fitted gate weights, expert matrices, model identity and single risk-only objective**. All architecture math is based on standard public numerical learning operations, not claimed novel academic methodology.

## Why we are trying this

A user's first 2000–2026 experiments (same `--no-trade-band 0.05`):
- Original dual-head model **with its return signal disabled at allocation time**: 220.13% cumulative simulated return, Sharpe 0.974, max drawdown -12.77%, accumulated trading notional/initial equity ~25.29x.
- Traditional trailing volatility with identical no-trade and independent hard risk checks: 216.83%, Sharpe 1.011, max drawdown -12.48%, turnover ~30.71x.
- The *dual-head* return forecast failed to beat a prediction of zero forward return in 855 noisy, correlated forecast cases; its risk MAE ~0.04265 versus 0.04397 trailing-risk baseline.

The next question is **whether independent risk-only training helps**, not how to maximize past Sharpe by repeated hyperparameter searches on an already examined history. RiskNet v2 has **no claimed 2000–2026 return, Sharpe or forecast MAE** before user-side training.

## Original RiskNet architecture

The model computes eight past-only asset/market features plus one-hot asset ID. A learned 3-way softmax gate combines 3 separate expert output scores. It learns **one target only**: log annualized forward root-mean-square daily return over the next H days. Its objective is a robust Huber loss, penalizing underpredicted realized risk 40% more than equally sized overprediction, plus weight regularization. It uses seeded initial weights, manually derived gradients, normalized targets, gradient clipping and a best training loss checkpoint.

The model is fitted locally from historical observations, with all target horizons required to mature by each simulated signal. Predictions arrive at a prior close, hypothetical portfolio orders execute at next close, and subsequent holding returns accrue afterward. Each forecast passes the exact same hard maximum single-asset concentration, forecast vol, daily tail risk, stress shock, drawdown and maximum 1x risky exposure checks as the existing traditional model. The unchanged risk-only allocation map is an inverse-risk sleeve with AtlasPM name caps and a 5% safe no-trade corridor when selected.

### Windows PowerShell — train your actual 2000–2026 history

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[native]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_atlas_risknet*.py" -v

# NEW: truly trained Atlas RiskNet v2 single risk head, not the old dual architecture.
.\.venv\Scripts\python.exe -m atlaspm.cli atlas-native-backtest --architecture risknet --return-head-strength 0 --file local_data\market_2000_2026.csv --feature-days 20 --forecast-days 5 --min-train 756 --train-window 504 --sample-stride 2 --epochs 40 --rebalance-days 21 --no-trade-band 0.05 --max-exposure 1 --output local_data\atlas_risknet_v2_2000_2026.json

# Optional sanity comparator: explicitly fit the old dual head while leaving its return predictions out of allocations.
.\.venv\Scripts\python.exe -m atlaspm.cli atlas-native-backtest --architecture dual --return-head-strength 0 --file local_data\market_2000_2026.csv --feature-days 20 --forecast-days 5 --min-train 756 --train-window 504 --sample-stride 2 --epochs 40 --rebalance-days 21 --no-trade-band 0.05 --max-exposure 1 --output local_data\atlas_dual_return_off_2000_2026.json
```

Read the two outputs and the matched baseline without uploading the raw model weights:
```powershell
$risk = Get-Content .\local_data\atlas_risknet_v2_2000_2026.json -Raw | ConvertFrom-Json
$dual = Get-Content .\local_data\atlas_dual_return_off_2000_2026.json -Raw | ConvertFrom-Json

foreach ($x in @(
    @{Name="Atlas RiskNet v2"; S=$risk.performance.atlas_native},
    @{Name="Atlas Dual Return-Off"; S=$dual.performance.atlas_native},
    @{Name="Traditional Same Band"; S=$risk.performance.naive_trailing_vol_risk}
)) {
    [PSCustomObject]@{
        Model = $x.Name
        ReturnPct = [math]::Round(100*$x.S.total_return,2)
        Sharpe = [math]::Round($x.S.sharpe_zero_risk_free_assumption,3)
        MaxDDPct = [math]::Round(100*$x.S.max_drawdown,2)
        Turnover = [math]::Round($x.S.cumulative_traded_notional_over_equity,2)
        Costs = [math]::Round($x.S.total_cost_equity_index_units,5)
    }
}
$risk.forecast_validation | Format-List
$risk.execution_summary | Format-List
```

The report includes `architecture_choice=risknet`, `return_prediction_head_trained=false`, `return_mae_annualized=null`, `return_forecast_better_than_zero_baseline=null`, native risk forecast MAE, and actual candidate/held target risk checks. A zero-return *assumption* is used only for the neutral allocation rule; the report must not present it as a learned return forecast. Last trained parameters are stored privately under ignored `local_data/`.

## Experimental guardrails

- **This is not independent final validation.** The 2000–2026 interval has been used repeatedly to develop AtlasPM; even v2 comparisons risk adaptive overfitting. Lock a fresh future and/or independent provider, unexamined instruments and dates before reporting deployable improvement.
- Higher total historical return alone is insufficient. Compare all three on identical dates, annualized risk, max drawdown, turnover, fees and forward-risk MAE; test calibration and protection during tail regimes.
- A risk-only model should outperform a naive forecast in a proper rolling OOS study before risking capital. Study whether lower risk MAE actually translates to improved allocation.
- Adjusted-close prices ignore realistic order fills, broker spreads, cash yields, tax and fund-specific trading windows; security histories may have backfilled vendor revisions.
- No IBKR paper/live orders, borrowed money or leverage above 1x are enabled. All institutional financing/risk gate booleans remain false. No claim of institutional proprietary sponsorship.
