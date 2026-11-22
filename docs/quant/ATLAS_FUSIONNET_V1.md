# AtlasPM v0.7.0 — FusionNet prototype after research on 30 public models

Read the separate **30-paper/model research matrix** first: [MODEL_RESEARCH_ATLAS_30.md](MODEL_RESEARCH_ATLAS_30.md). The directory explicitly distinguishes forecasting, financial risk, uncertainty and portfolio-allocation methods. Thirty sources were studied and compared for *ideas*, **not** thirty external model codebases installed/trained. Neither paper scores nor third-party pretrained weights have been imported.

## What was learned, what was ACTUALLY implemented

- **GARCH, EGARCH, GJR-GARCH, HAR-RV + TimeMixer/N-HiTS:** motivated fixed 5, 10 and lookback-day asset mean, RMS volatility, downside RMS and largest daily loss features. Shared cross-asset risk-context features include basket downside risk, market-wide negative-day frequency and correlations. **No GARCH likelihood or TimeMixer original block is run**.
- **TFT + iTransformer:** motivated a learned *three-expert gated network* over multiscale shared cross-asset inputs. Gate probabilities are *latent numerical mixture weights*, not validated labels for crash/bull/bear markets.
- **CAViaR, EVT, CVaR:** motivated training **two genuinely distinct risk targets**: (1) forward log annualized RMS daily return and (2) forward log annualized RMS of only negative daily returns. A robust asymmetric Huber loss places more penalty on underpredicting downside. This is **NOT** a calibrated VaR, expected shortfall or EVT extreme-quantile model, since these targets do not estimate a lower conditional quantile.
- **DCC, covariance shrinkage, HRP:** motivated an original, modestly regularized **past-only sample correlation** allocator that penalizes coincident asset risk and high individual downside exposure, constrained by per-name maximum and independent original risk gate. Off-diagonal correlations are multiplied by a fixed 0.65 (35% shrink toward identity). This is **NOT** DCC likelihood, fitted Ledoit-Wolf shrinkage, hierarchical clustering or HRP.
- **DLinear, TSMixer, TiDE:** motivate testing whether additional complexity contributes anything over simple fully observed risk baselines; three controlled allocation modes isolate effect of training vs downside and correlation choices.
- **Chronos, Moirai, TimesFM, TimesFM-3, DeepLOB:** are **research-only future comparators** requiring license, provenance and (for DeepLOB) order-book data, not imported pretrained models.

The model itself is an **original AtlasPM NumPy gate-and-expert implementation**, fit **from a fixed random seed for each past decision** with custom backpropagation. The weights, two risk targets, history and outputs remain in ignored local `local_data/`. There is no forward expected-return head, no alpha or market direction guarantee.

### 3 controlled modes with identical fitted FusionNet

| CLI mode | Historical daily data used in allocation | Purpose |
|---|---|---|
| `--fusion-allocator-mode inverse_risk` | ONLY FusionNet forward RMS risk estimates | Isolate effects of new predictor, relative to RiskNet v2 |
| `--fusion-allocator-mode downside` | FusionNet overall risk + downside RMS | Test incremental downside target in asset weighting |
| `--fusion-allocator-mode shrinkage` | Above + regularized past-only correlation | Test cross-asset exposure and diversification effects |

The **same neural architecture/training** is fitted in all three modes; only the last allocation formula differs. The risk caps, simulated fee/slippage, stress and max gross 1x, prior-signal decision, same no-trade corridor and benchmark remain the same. Risk-based sleeve weights are proposed but CANNOT bypass the original `_select_allocation` constraints or independent risk check required before skipping a trade.

## Reproducible Windows PowerShell on 2000–2026 research history

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[native]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_atlas_fusionnet*.py" -v

# v0.7.0 new original FusionNet multi-scale dual-risk model / regularized cross-asset sleeve
.\.venv\Scripts\python.exe -m atlaspm.cli atlas-native-backtest --architecture fusion --return-head-strength 0 --fusion-allocator-mode shrinkage --file local_data\market_2000_2026.csv --feature-days 20 --forecast-days 5 --min-train 756 --train-window 504 --sample-stride 2 --epochs 40 --rebalance-days 21 --no-trade-band 0.05 --max-exposure 1 --output local_data\atlas_fusion_shrinkage_2000_2026.json

# Predictor-only ablation: identical model but simple inverse risk sleeve
.\.venv\Scripts\python.exe -m atlaspm.cli atlas-native-backtest --architecture fusion --return-head-strength 0 --fusion-allocator-mode inverse_risk --file local_data\market_2000_2026.csv --feature-days 20 --forecast-days 5 --min-train 756 --train-window 504 --sample-stride 2 --epochs 40 --rebalance-days 21 --no-trade-band 0.05 --max-exposure 1 --output local_data\atlas_fusion_inverse_2000_2026.json

# Downside-only asset allocator ablation
.\.venv\Scripts\python.exe -m atlaspm.cli atlas-native-backtest --architecture fusion --return-head-strength 0 --fusion-allocator-mode downside --file local_data\market_2000_2026.csv --feature-days 20 --forecast-days 5 --min-train 756 --train-window 504 --sample-stride 2 --epochs 40 --rebalance-days 21 --no-trade-band 0.05 --max-exposure 1 --output local_data\atlas_fusion_downside_2000_2026.json
```

All calls are explicit one-shot research commands; no remote pretraining is downloaded. **No returns are yet known**. This period has already been used to select AtlasPM's development architecture and includes the crises studied in detail. Results here are exploratory, not untouched validation.

### Compare fairly with already trained RiskNet v2 and conventional baseline

```powershell
$full = Get-Content .\local_data\atlas_fusion_shrinkage_2000_2026.json -Raw | ConvertFrom-Json
$inv  = Get-Content .\local_data\atlas_fusion_inverse_2000_2026.json -Raw | ConvertFrom-Json
$down = Get-Content .\local_data\atlas_fusion_downside_2000_2026.json -Raw | ConvertFrom-Json
$old  = Get-Content .\local_data\atlas_risknet_v2_2000_2026.json -Raw | ConvertFrom-Json

@(
    @{Name="FusionNet correlation"; R=$full.performance.atlas_native},
    @{Name="FusionNet downside"; R=$down.performance.atlas_native},
    @{Name="FusionNet inverse"; R=$inv.performance.atlas_native},
    @{Name="RiskNet v2 locked"; R=$old.performance.atlas_native},
    @{Name="Traditional equal rules"; R=$full.performance.naive_trailing_vol_risk}
) | ForEach-Object {
    [PSCustomObject]@{
        Model = $_.Name
        ReturnPct = [math]::Round(100*$_.R.total_return,2)
        Sharpe = [math]::Round($_.R.sharpe_zero_risk_free_assumption,3)
        MaxDDPct = [math]::Round(100*$_.R.max_drawdown,2)
        Turnover = [math]::Round($_.R.cumulative_traded_notional_over_equity,2)
        Fees = [math]::Round($_.R.total_cost_equity_index_units,5)
    }
} | Format-Table -AutoSize

$full.forecast_validation | Format-List
```

The `forecast_validation.downside_rms_mae_annualized` metric assesses the new tail-asymmetry *proxy*. RiskNet's ordinary RMS MAE was previously about 0.04143 vs conventional baseline ~0.04397. This is not a VaR/CVaR backtest, crash-probability calibration or statistically robust independent replication. When comparing, note that FusionNet simultaneously changes both features and loss; **its separate allocator modes only isolate allocation**, not individual predictor components. To attribute new prediction performance to any one of 30 references, do a future **feature/loss ablation** with all else held fixed.

## Non-negotiable experimental and safety limits

- No forward/unreleased inputs at a decision; only supervised labels with end date at or before that decision. Delayed maturity, past-only standardization, no data-snooping.
- Sample periods 2000–2026 are already repeatedly inspected; do NOT label a new architecture's better return on this period as truly independent out-of-sample success. Fix specification before a future observation sample accumulates.
- A correlation-aware weight change might lose more money or increase crises drawdown. 2008/2020/2022 were already used to discover the old model's higher equity exposure; do not set loss parameters or optimize weights to match these outcomes.
- The current observed-security universe is only QQQ, SPY, VFITX; 3 assets, several share correlated US-equity beta. Changes might not generalize to live, to ETF mutual-fund close schedules, to transaction volumes or future data vintages.
- Keep optional `native` dependency as NumPy; do NOT import external pre-trained model weights or third-party source code from study register without separate license inspection. 
- Cash yield modeled as zero; no actual IBKR broker account/order execution, margin debt, automated real-time action or institutional risk readiness. All flags remain false.
