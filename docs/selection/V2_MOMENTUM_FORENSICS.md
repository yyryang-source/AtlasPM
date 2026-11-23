# AtlasPM V2 12–1 Momentum: extreme Top-10 outcome diagnostics

This tool is for **investigating** a surprising result in the user's
local 5,830-current-listed-stock, 2024–2026 experiment. It does not
retrain V2, alter alpha-gate eligibility, or touch portfolios/brokers.

## Observed V2 results to investigate

With the user's actual AtlasPM V2 tie-safe report:

| Statistic | Validation (4 decisions) | Test (5 decisions) |
| --- | ---: | ---: |
| V2 mean Spearman IC | +0.01382 | -0.01774 |
| Simple 12–1 momentum mean Spearman IC | +0.13662 | +0.11176 |
| Simple momentum Top-10 mean next-21-session SPY excess | -15.19% | -11.87% |
| Simple momentum Top-10 mean minus same-stock-pool mean | -15.38% | -9.93% |
| V2 Top-10 dates without rank-cutoff ties | 0 | 0 |

A positive overall cross-sectional Spearman IC and negative **extreme**
Top-10 return are mathematically compatible: the central ranking can
be directionally correct even if a few sky-high momentum names crash.
It is also possible that quote sources, corporate actions, symbol
identity or missing liquidity data are misleading. **Do not assume**
which explanation is correct until inspecting the individual
cohorts/tickers. Neither the V2 model nor simple momentum has passed
research validation.

## Local Windows-only diagnostic (no vendor calls)

Run in the **same** repo with the **same** 2025–2026 split and label
parameters used in the already saved training report. This command
reconstructs the training feature/label cohorts, but **does not fit
or change a model** and does not need network access:

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git pull --ff-only origin main

.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_v2_momentum_diagnostics.py" -v

.\.venv\Scripts\python.exe -m atlaspm.cli stock-ai-diagnose-momentum `
    --prices local_data\us_equity_prices.csv `
    --benchmark-prices local_data\market_2000_2026.csv `
    --start 2025-01-02 `
    --train-end 2025-12-31 `
    --validation-end 2026-05-29 `
    --test-end 2026-11-20 `
    --horizon-sessions 21 `
    --sample-every-sessions 21 `
    --top 10 `
    --output local_data\v2_momentum_diagnostics.json

$d = Get-Content local_data\v2_momentum_diagnostics.json -Raw | ConvertFrom-Json
$d.test | Select-Object decision_dates,unambiguous_decision_dates,mean_top_n_forward_spy_excess,mean_top_n_minus_pool_mean,total_extreme_momentum_picks,total_forward_losses_over_50pct | Format-List
$d.test.cohorts | ForEach-Object {
    Write-Host ("Decision {0}, exit {1}, mean SPY-excess {2:P2}" -f $_.decision_date,$_.label_exit_date,$_.top_n_outcome.average_forward_spy_excess)
    $_.worst_five_selected | Select-Object symbol,momentum_12_1,forward_stock_return,forward_spy_excess_return | Format-Table -AutoSize
}
```

If the report's mean test SPY excess doesn't reproduce **-0.1187283**
and its test decision-date count doesn't equal **5**, double-check the
benchmark price source, date ranges, sampling parameters, and original
CSV version; do not try fitting an updated model until the discrepancy
is explained.

The JSON contains exact decision, next-close entry and forward-label
exit dates; 12–1 trailing momentum and realized SPY-relative and
absolute stock return for each selected ticker; the five worst
per-decision picks; and counts of exploratory flags for forward losses
above 50% or momentum above 300%.

**An extreme flag is not evidence of wrong vendor data.** Inspect
flagged names against corporate actions, official listing identities,
reverse splits, price adjustments and liquidity. Never delete
negative-return observations simply to improve a backtest. The
unverified current-listing universe remains survivor-biased and does
not establish historical all-US selection ability.

## Next methodological work

Collect more history with point-in-time listing membership, including
failures/delistings; verify liquidity/market cap and corporate action
vintages; fit and evaluate across more independent dates and unseen
periods. Consider robust tail handling **only on training folds** with
a fixed prospective evaluation protocol. Test and validation in this
experiment have already been inspected repeatedly, so this report is
descriptive research, not a fresh alpha test.
