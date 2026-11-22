# AtlasPM v0.6.3 — Matched RiskNet robustness audit

## What the user's current results establish

A historical Atlas RiskNet v2 research fit to the user's local 2000–2026 price CSV (3 assets, 5,981 simulated held sessions, 285 monthly candidate rebalances) reported:

| Series, identical safe 5% no-trade band | Simulated cumulative return | Zero-RF Sharpe | Maximum drawdown | Cumulative turnover |
|---|---:|---:|---:|---:|
| **Atlas RiskNet v2** | **305.13%** | **1.031** | -12.56% | **23.67×** |
| Atlas dual-head v1 return-off | 220.13% | 0.974 | -12.77% | 25.29× |
| Traditional trailing risk | 216.83% | 1.011 | **-12.48%** | 30.71× |

Its annualized forward-risk forecast MAE was **0.0414258** versus the traditional **0.0439653**, approximately **5.8% lower**. This is a useful research signal, **not proof of an investable edge**. The 2000–2026 interval has already influenced multiple AtlasPM model iterations; the Sharpe increment of 0.020 over traditional control is small; the max drawdown was slightly worse.

The prior RiskNet v2 CLI summary displayed a v1 walk-forward method string even though it used a separate v2 model family. The new report in v0.6.3 fixes that metadata for later runs without changing the trained model or backtest mechanics.

## New: check paired robustness without training again

`atlas-native-audit` reads the **two existing private JSON reports** and validates all security, data and transaction-cost assumptions before comparison. It requires exactly matched initial data coverage, same assets, signal/rebalance settings, target volatility, drawdown guard, shock loss, tail cap, fees, slippage, no-trade band and exposure limits; also asserts the *traditional benchmark equity curve is exactly the same in both runs*. If anything differs, the audit fails rather than silently treating unmatched strategies as comparable.

It reads full daily cash/asset equity curves under `local_data/`, validates nonnegative cash and 0–1x positions, confirms daily ledgers reconcile with cumulative performance, then calculates:

1. Full period, **each calendar year's** compounded return, sample annualized volatility, sample zero-risk-free-rate Sharpe and maximum drawdown.
2. Explicitly labeled predefined historical windows: dotcom 2000–2002, 2007–2009 credit crisis, February–March 2020 COVID crash, full-year 2022, and the already-studied 2021–2026 recent era. Windows without at least 10 held sessions are marked unavailable; the dotcom era **will have no held-out trades if your initial training finishes only in 2003**.
3. **Paired** circular moving-block bootstrap distributions for RiskNet vs traditional and RiskNet vs dual (default 500 replicates of 21-session blocks), showing 95% resampled ranges of the annualized arithmetic excess daily return and Sharpe difference. This preserves local time dependence more than independent one-day sampling, but is **exploratory only**: it is not a valid final significance or data-snooping correction.

### Windows PowerShell

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[native]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_atlas_robustness_audit.py" -v

.\.venv\Scripts\python.exe -m atlaspm.cli atlas-native-audit --risknet-report local_data\atlas_risknet_v2_2000_2026.json --dual-report local_data\atlas_v061_risk_only_2000_2026.json --block-days 21 --bootstrap-reps 500 --output local_data\atlas_v063_robustness_audit.json
```

Inspect private output without publishing model parameters, positions, or account data:

```powershell
$a = Get-Content .\local_data\atlas_v063_robustness_audit.json -Raw | ConvertFrom-Json

$a.paired_uncertainty.risknet_minus_traditional | Format-List
$a.paired_uncertainty.risknet_minus_dual | Format-List

# Annual figures of interest:
foreach ($year in @("2008","2009","2020","2022","2024","2025","2026")) {
    [PSCustomObject]@{
        Year = $year
        RiskNetPct = [math]::Round(100*$a.diagnostics.risknet.calendar_years.$year.total_return,2)
        DualPct = [math]::Round(100*$a.diagnostics.dual_return_off.calendar_years.$year.total_return,2)
        TraditionalPct = [math]::Round(100*$a.diagnostics.traditional.calendar_years.$year.total_return,2)
    }
} | Format-Table -AutoSize

# Compare crisis drawdowns:
$a.diagnostics.risknet.predefined_market_windows | Format-List
```

Run `--block-days 63` as a *sensitivity diagnostic* with another private output file to see whether the uncertainty range expands with longer dependent blocks. Do **not** use bootstrap results to retrofit trading parameters onto already seen years.

## Scope and limitations

The CLI **never refits, selects or trades a model**. It does not call the broker, forecast prices, adjust risk budgets or touch existing model files. The reported full historical comparison is post-selection. **Do not confuse a resampling interval over past daily outcomes with a fresh untouched final test** or a guarantee that future performance will follow.

Research assumptions to resolve later: independently sourced market prices, adjusting historical mutual-fund valuations, dividend data integrity, cash/T-bill yield time series, execution delay, spreads and live fills, taxes, strategy capacity and liquidity, and forward paper tests. No IBKR authorization, financing debt verification, risk approval or paper/live orders; all flags remain false.
