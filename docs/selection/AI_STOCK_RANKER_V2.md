# AtlasPM AI Stock Ranker V2: pure cross-sectional Learning-to-Rank

**This is a stock PICKER only**. No order, broker API, weights, leverage,
trade, risk budget, stop-loss or market-maker functions.

V1 trained a LightGBM regression model to predict a percentage return and
scored 25/38 securities with an identical +0.4924% forecast as of Nov 20 2026.
Consequently its Top 20 ranking was undefined (Top N cut through that tie).

V2 is a SEPARATE research experiment motivated by Microsoft's Qlib stock
ranking workflow and LightGBM's official rank_xendcg objective. Original
AtlasPM code; no Qlib pretrained model or bank proprietary weight files.

## Method

- Reuse V1's nine historical decision-close price features: 12–1, 6–1,
  3–1 and 1-month momentum, price-to-SMA200 / SMA50, annualized 63/21
  session volatility, past 253-session drawdown.
- For each eligible decision date, group stocks and grade realized future
  relative returns within that SAME decision-date cohort into five
  ordered relevance levels 0..4.
- Fit LightGBM objective=rank_xendcg; group boundaries are explicitly
  passed to Dataset. Early-stop based on validation NDCG@5/10.
- Purge forward-return labels crossing train/validation/test cutoff dates,
  and use *next session close* as the training entry for forward returns.
- Evaluate **held-out date-wise Spearman IC** versus 12–1 momentum,
  held-out Top-N realized future excess versus SPY and date-wise
  stock-pool average, plus rank-score uniqueness and boundary ties.
- Model scores are uncalibrated **ranking utilities**, never converted to
  predicted % returns or probable upward movement.
- Score ties share competition ranks. A Top-N cut through tied names
  is flagged REVIEW_REQUIRED; no fake alphabetical preference.

## Windows local training — use existing 38-name dataset first

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git fetch origin
git switch --track origin/feature/ai-stock-learning-to-rank-v2
# If already exists: git switch feature/ai-stock-learning-to-rank-v2
.\.venv\Scripts\python.exe -m pip install -e ".[ml]"

.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ai_stock_ranker_v2.py" -v

.\.venv\Scripts\python.exe -m atlaspm.cli stock-ai-train-v2 --prices local_data\us_stocks_2000_2026.csv --benchmark-prices local_data\market_2000_2026.csv --start 2000-01-01 --train-end 2018-12-31 --validation-end 2022-12-30 --test-end 2026-11-20 --top 10 --num-boost-round 300 --model-output local_data\atlas_stock_ranker_v2.txt --report local_data\atlas_stock_ranker_v2_report.json

$r = Get-Content local_data\atlas_stock_ranker_v2_report.json -Raw | ConvertFrom-Json
$r.training | Format-List
$r.validation | Format-List
$r.test | Format-List

# Score stocks from the NEW V2 model (not the V1 model)
.\.venv\Scripts\python.exe -m atlaspm.cli stock-ai-rank-v2 --prices local_data\us_stocks_2000_2026.csv --benchmark-prices local_data\market_2000_2026.csv --model local_data\atlas_stock_ranker_v2.txt --as-of 2026-11-20 --top 20 --output local_data\atlas_stock_ranker_v2_rankings.json

$r = Get-Content local_data\atlas_stock_ranker_v2_rankings.json -Raw | ConvertFrom-Json
$r.ranking_diagnostics | Format-List
$r.selected | Select-Object rank,symbol,ranking_score,tie_count | Format-Table -AutoSize
```

## Acceptance/rejection

V1 baseline on same 38 survivor-stock universe (observed in prior run):
test mean Spearman IC 0.03895; simple momentum -0.00195,
mean Top10 next-21-session excess above same-pool average 0.00670.
V1 model: 20 saved trees, 14/38 displayed forecast levels, 25-way
tie and Top20 ambiguity. These are NOT Wall Street institutional alpha.

**Only consider V2 a promising replacement if** its test IC improves
without degrading validation stability, its Top10 spread is positive,
and the Top20 tied-score boundary resolves. A higher number of unique
scores alone is **not** evidence of better stock forecasts.

All 2023–2026 sample periods have been examined in past research;
this is not untouched future holdout. Current 38 survivors exclude
historical delisted and failed stocks, and Yahoo back-adjusted histories
can suffer corporate-action revision leakage. Full US-stock training
requires larger survivorship-aware datasets and possibly a streaming
data loader, but these limitations do NOT change the pure-stock-picker
scope. Existing Atlas portfolio/risk/trading components stay separate.

## Full-US research experiment (November 2026) — tie-safe evaluation

After the current-listed US market quote expansion was audited as
`READY_FOR_RESEARCH_REVIEW` with 5,830 cached symbols and 5,404
history/freshness-qualified stocks, the user trained the V2 ranker
on prices from 2024-01-01 through 2026-11-20. This is **not**
an unbiased historical US market membership or a live-trade approval.

First run (reported in local, NOT GitHub-shared JSON):
- Train labeled rows 53,857; validation 20,399; test 26,382
- Validation 4 decision dates; test 5 decision dates
- Early-stopped best LightGBM iteration 4/150
- Validation/test mean Spearman IC: +0.01382 / **-0.01774**
- Simple 12-1 momentum validation/test IC: +0.13662 / **+0.11176**
- Distinct displayed score-level fraction: about 3% per date
- **Every** validation and test date's Top-10 cutoff crossed a tied
  score group. Earlier Top-10 mean future SPY-excess and pool-spread
  figures used ticker-alphabetic tie breaking and **cannot support
  a claim of selection alpha**.

Starting with the tie-safe evaluation fix, the V2 training report:

1. Reports Top-N future SPY excess and same-pool spread **only** from
   dates where the rank cutoff is unambiguous. If all dates have boundary
   ties, these fields are JSON `null`, not a made-up return.
2. Preserves the *old* tie-broken numbers solely under
   `legacy_symbol_tiebroken_top_n_diagnostic`, marked **not a valid
   portfolio result**.
3. Compares simple 12-1 momentum Top-N returns on matching test
   cohorts, plus its existing date-wise Spearman IC.
4. Emits `model_research_review.status=REVIEW_REQUIRED` if the
   validation/test sample includes fewer than 12 decision dates,
   ambiguous Top-N cutoffs, or the model's test IC is non-positive or
   no better than simple momentum. Twelve is a *diagnostic minimum*,
   **not evidence of statistically sufficient testing**.
5. Does **not** turn on orders, allocate capital, or approve alpha if
   the result is `PRELIMINARY_RESEARCH_REVIEW`.

To regenerate the **new, tie-safe** metrics on local 2024–2026 data
without overwriting the original 38-name V2 files:

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git pull --ff-only origin main

.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_v2_tie_safe_evaluation.py" -v

.\.venv\Scripts\python.exe -m atlaspm.cli stock-ai-train-v2 `
    --prices local_data\us_equity_prices.csv `
    --benchmark-prices local_data\market_2000_2026.csv `
    --start 2025-01-02 `
    --train-end 2025-12-31 `
    --validation-end 2026-05-29 `
    --test-end 2026-11-20 `
    --horizon-sessions 21 `
    --sample-every-sessions 21 `
    --num-boost-round 150 `
    --top 10 `
    --model-output local_data\atlas_stock_ranker_us_v2.txt `
    --report local_data\atlas_stock_ranker_us_v2_report.json

$r = Get-Content local_data\atlas_stock_ranker_us_v2_report.json -Raw | ConvertFrom-Json
$r.model_research_review | Format-List
$r.validation | Select-Object decision_dates,mean_spearman_ic,simple_momentum_mean_spearman_ic,top_n_ambiguous_decision_dates,top_n_unambiguous_decision_dates,mean_top_n_forward_excess_return | Format-List
$r.test | Select-Object decision_dates,mean_spearman_ic,simple_momentum_mean_spearman_ic,top_n_ambiguous_decision_dates,top_n_unambiguous_decision_dates,mean_top_n_forward_excess_return | Format-List
$r.test.simple_momentum_top_n | Format-List
```

Prerequisite: `local_data/market_2000_2026.csv` must contain
`date,symbol,adjusted_close` for SPY through the exact 2026-11-20
NYSE session. The new scorecard needs an actual rerun to reconstruct
per-date Top-10 boundary decisions; it cannot reverse engineer
the correct Top-N figures from the old summary-only report.

**Next research priorities:** secure a survivorship-aware point-in-time
training universe, collect more completed decision periods, use
non-overlapping/appropriately purged forward-return labels, and
compare V2 against the momentum baseline on untouched future windows.
Do not repeatedly optimize against the same 2026 test period.

