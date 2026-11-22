# AtlasPM AI Stock Ranker: Qlib-inspired supervised research

**Purpose:** choose and rank stock candidates only. The existing Atlas
RiskNet, RegimeNet, FusionNet, optimizer, read-only IBKR gateway and
independent execution permissions are unchanged.

## External research methods, not copied models

- [Microsoft Qlib](https://github.com/microsoft/qlib) (MIT):
  equity factors, supervised learning, cross-sectional IC research.
- [Microsoft RD-Agent](https://github.com/microsoft/RD-Agent):
  autonomous factor and model research, later stage.
- [AI4Finance FinRL-X](https://github.com/AI4Finance-Foundation/FinRL-Trading)
  (Apache-2.0): modular stock selector architecture.
- [Goldman Sachs GS Quant](https://github.com/goldmansachs/gs-quant)
  (Apache-2.0): public statistical toolkit, NOT proprietary bank alpha.
- [QuantConnect LEAN](https://github.com/QuantConnect/Lean)
  (Apache-2.0): historical event and execution modeling reference.

The source here is AtlasPM original code; no proprietary bank
model weights or Qlib pretrained US model. LightGBM learns nine
past-price factors to predict forward SPY-relative return.

## Windows PowerShell 5.1

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git fetch origin
git switch --track origin/feature/ai-stock-ranker-lightgbm-v1
# If branch exists: git switch feature/ai-stock-ranker-lightgbm-v1
.\.venv\Scripts\python.exe -m pip install -e ".[ml]"

# Local tests including actual LightGBM if installed
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ai_stock_ranker.py" -v

# Long command is intentionally one line for PowerShell 5.1 compatibility
.\.venv\Scripts\python.exe -m atlaspm.cli stock-ai-train --prices local_data\us_stocks_2000_2026.csv --benchmark-prices local_data\market_2000_2026.csv --start 2000-01-01 --train-end 2018-12-31 --validation-end 2022-12-30 --test-end 2026-11-20 --sample-every-sessions 21 --horizon-sessions 21 --num-boost-round 180 --model-output local_data\atlas_ai_stock_lgbm.txt --report local_data\atlas_ai_stock_train.json

# Review actual validation, test IC, sample counts
$r = Get-Content local_data\atlas_ai_stock_train.json -Raw | ConvertFrom-Json
$r.split_counts | Format-List
$r.validation | Format-List
$r.test | Format-List

# Inference on same 38 survivors for basic integration validation
.\.venv\Scripts\python.exe -m atlaspm.cli stock-ai-rank --prices local_data\us_stocks_2000_2026.csv --benchmark-prices local_data\market_2000_2026.csv --model local_data\atlas_ai_stock_lgbm.txt --as-of 2026-11-20 --top 20 --output local_data\atlas_ai_stock_rankings.json

$r = Get-Content local_data\atlas_ai_stock_rankings.json -Raw | ConvertFrom-Json
$r.selected | Select-Object rank,symbol,predicted_forward_excess_percent | Format-Table
```

## Leakage controls and metrics

Decision-close factors: 12–1, 6–1, 3–1, one-month momentum,
price/SMA200, price/SMA50, 63/21-session volatility and max drawdown.
Requires a current decision-date close and at least 253 prior valid closes.
Missing current, entry or exit quotes are not invented. No history gap
greater than 14 calendar days.

Supervised label = stock return from next benchmark-session close to the
close 21 benchmark sessions later, **minus** SPY's same-date return.
Monthly 21-session cohorts by default. Chronological train labels exit
by Dec 2018, validation labels exit by Dec 2022, test labels exit by
Nov 2026; crossing labels are purged. Validation may tune early stopping,
but test labels do not train weights or determine early stopping.

Scorecards contain date-wise Spearman information coefficient (IC)
and simple 12–1 momentum IC; Top N mean future excess return relative
to SPY and the same universe mean. These are sample research metrics,
NOT a filled and transaction-costed real portfolio strategy.

LightGBM model saved as text plus feature schema, metadata and SHA256
check. Ranking refuses metadata mismatch, altered model file, wrong
training benchmark or inference prior to validation cutoff.

## Score-tie diagnostics — avoid misleading Top N

Gradient boosting can assign the **same score** to many securities. Equal
displayed predictions now get identical *competition ranks*. A deterministic
alphabetical display order **does not** imply an AI preference within a tie.
The saved model does NOT have to be retrained for this reporting improvement.

- selected.rank: competition rank, e.g. 1, 2, 3, 3, 3
- selected.display_position: list position only (not a different AI rank)
- selected.tie_count and selected.tied: show indistinguishable scores
- ranking_diagnostics: distinct displayed scores, largest tied group, how
  many stocks are tied, and whether the requested Top N cuts through a tie
- model_diagnostics: best_iteration, saved tree count, per-feature gain
  and split counts (diagnostics, not guaranteed causal explanations)

If ties_cross_top_n_cutoff=true, the first N output rows do not comprise
a uniquely ranked Top N. Mark rank_quality REVIEW_REQUIRED and do not
interpret alphabetical order as AI portfolio selection.

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git fetch origin
git switch --track origin/feature/ai-ranker-tie-diagnostics-v1
# If the branch already exists, use git switch feature/ai-ranker-tie-diagnostics-v1
.\.venv\Scripts\python.exe -m pip install -e ".[ml]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ai_stock_ranker.py" -v

# Re-score the existing trained model, no new training necessary
.\.venv\Scripts\python.exe -m atlaspm.cli stock-ai-rank --prices local_data\us_stocks_2000_2026.csv --benchmark-prices local_data\market_2000_2026.csv --model local_data\atlas_ai_stock_lgbm.txt --as-of 2026-11-20 --top 20 --output local_data\atlas_ai_stock_rankings_v2.json

$r = Get-Content local_data\atlas_ai_stock_rankings_v2.json -Raw | ConvertFrom-Json
$r.ranking_diagnostics | Format-List
$r.model_diagnostics | Format-List
$r.selected | Select-Object rank,display_position,symbol,predicted_forward_excess_percent,tie_count | Format-Table -AutoSize
```

## Important limits

A smoke-test model trained on today's 38 surviving large caps is NOT
validated for all US securities. Full market requires enough history
and delisted-company membership, IPO eligibility, point-in-time financials,
data-quality checks and a representative split. Adjusted Yahoo data
are retrospectively revised; historical delisted stocks are excluded
by the current listing universe. Prior user investigations have already
inspected 2023–2026: not truly untouched test data.

Large 26-year full-universe CSVs may exceed RAM: first ML implementation
loads complete stock histories into Python memory. A streaming dataset
backend must precede production-scale historical training.

Planned (not yet implemented): fundamental/valuation factors,
sector/size neutrality, multiple models, rolling retraining, calibrated
uncertainty, and an API contract for supplying candidate rankings to
AtlasPM existing portfolio/risk control stack. No broker orders here.
