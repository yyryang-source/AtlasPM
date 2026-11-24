# Factor V3 — offline standardization, neutralization and composite laboratory

**Status:** Research-only. Uses existing AtlasPM V1 factor Parquet files and V2
dated-label methodology. Nothing is uploaded to GitHub, downloaded, traded, or
made eligible for ML training by this module. Inputs, outputs and any optional
industry mapping stay under gitignored `local_data/`.

## Windows PowerShell (download-safe)

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git switch main
git pull --ff-only origin main
$py = '.\.venv\Scripts\python.exe'
& $py -m pip install -e '.[lake]'
& $py -m unittest discover -s tests -p 'test_factor_v3.py' -v

# Fast inspection / plan only, no factor data scans or output writes.
& $py -m atlaspm.cli market-db factor-v3-analyze --start 2000-02-01 --end 2000-03-31 --factors mom_5d mom_21d volatility_21d

# Small local exploration using already materialized factor files:
& $py -m atlaspm.cli market-db factor-v3-analyze --start 2000-02-01 --end 2000-03-31 --factors mom_5d mom_21d --horizon 1 --exploratory --memory 2048MB --threads 2

# After consecutive months have been constructed (not while downloading):
& $py -m atlaspm.cli market-db factor-v3-analyze --start 2000-06-01 --end 2002-06-30 --factors mom_5d mom_21d mom_63d volatility_21d --horizon 5 --holdout-start 2002-01-01 --weighting ic_train --exploratory
```

Do not run exploratory scans on a large historical range while the raw market
download is busy. The default without `--exploratory` is plan-only. Reports
are saved locally to `local_data/factors/v3/reports/` by default. Set
`--no-save` for console output only. The optional `--save-scores`
creates Parquet in `local_data/factors/v3/scores/` **only on explicit
request** and marks every row as research-only with training/trading disabled.
No model-training or broker API consumes these files.

## 1. Standardization

For each trade date, quality-filtered stock cross-section and selected factor:

- Discard NULL/nonfinite factor values. Cap at that day's empirical
  `--winsor-tail 0.01` and 0.99 quantiles (1% winsorization).
- With **no** dated industry mapping, cross-sectional standardize:
  `z = (winsorized value - cross-sectional mean) / sample stddev`.
- Constant cross-sections, insufficient sector group observations, and days
  with fewer than `--min-symbols` (default 30) values are NULL, not zero.
- Return factor raw non-null count, standardized-row count and distinct
  standardized dates. Factor not standardized on enough dates is excluded
  from the multi-factor score **with an explicit reason**.

This is per-date transform, never fitted on future dates.

## 2. Dated industry neutralization (opt-in ONLY)

No trusted, point-in-time industry/classification vendor is currently
integrated. **Never use today's sector membership for year-2000 rows**.
If you have an independently curated effective-dated mapping, create a
local-only CSV:

```csv
symbol,industry,start_date,end_date
AAA,Technology,2000-01-01,2000-12-31
BBB,Energy,2000-01-01,2000-12-31
```

```powershell
& $py -m atlaspm.cli market-db factor-v3-analyze --start 2000-02-01 --end 2000-03-31 --factors mom_5d mom_21d --neutralize-industry --industry-csv local_data\my_dated_industries.csv --min-group-size 5 --exploratory
```

These sample CSV values are **illustrative, not real historical security data**.
`start_date` / `end_date` are inclusive; blank end date
indicates open-ended. Symbol/date overlaps in the file cause an error. Missing
industry mapping for **any quality-eligible security/day** in the requested
window causes the entire run to fail, not an "Unknown" fallback. Small
industry/date groups (fewer than 5 securities by default) yield NULL scores.
Given complete coverage, first winsorize globally, subtract the contemporaneous
within-industry mean, then z-standardize residuals globally.
Classification **accuracy is not certified** simply because the file loads;
historical security identifiers remain unverified.

## 3. Correlations and redundant-factor selection

For every factor pair, compute *cross-sectional Spearman* correlation separately
each trade date. Summarize both the equal-date signed correlation and the
mean of **absolute** date correlations so negative duplicates do not cancel
against positive ones. Require `--min-dates` (8 by default) and
`--min-symbols` paired observations per date. If insufficient, the
pair is marked UNKNOWN and **is not** claimed to be independent.

Output includes a signed symmetric correlation matrix (unknown pairs as NULL)
and per-pair mean absolute correlations used by the redundancy rule.
Greedy deduplication drops the next factor when mean absolute Spearman
meets/exceeds `--redundancy-threshold 0.85` with an already-kept
factor. Without a holdout, selection uses **user factor order and
unsupervised** correlations over the requested period. With holdout, both
correlation discovery and ranking priority use **pre-holdout dates only**.
Ranking priority may use absolute *training-matured* IC, never holdout IC.
Feature availability for selection is likewise checked only on pre-holdout dates
when a holdout is configured.

## 4. IC stability and train/test safety

Signal is known after T close; hypothetical entry = the next observed
global-market-session close T+1; hypothetical exit = T+h+1 close.
It is NOT a fillable trading simulation. Date-index stride h avoids
overlapping forward holding windows; a >7 calendar-day gap across any
forward label interval disqualifies it. Missing entry/exit securities are not
forward-filled.

Per-factor and composite: chronological daily Rank IC (Spearman), mean IC,
unannualized ICIR (mean / sample stdev), share of positive-IC dates,
sign consistency, trailing 20-observation IC mean and per-month IC averages.

With `--holdout-start`, **training** IC includes a signal only
if its forward label has **fully exited before the holdout date**.
Overlap/embargoed observations are counted and purged. Holdout IC is reported
separately. Selecting factors and start dates after inspecting outcomes
still biases validation. This is diagnostic, **not independent OOS
certification**. No multiple-hypothesis correction is implied.

## 5. Composite factor score

- `--weighting equal` (default): fixed equal **positive**
  weights across remaining nonredundant factors; does not learn returns.
- `--weighting ic_train --holdout-start YYYY-MM-DD`: weights
  proportional to signed **matured, pre-holdout mean Rank IC**. A negative IC
  reverses the factor direction. If any selected factor lacks sufficient
  matured IC, no composite is generated (no guessed/fallback weights).
- All selected factor z-scores must be non-NULL to receive a composite.
  Missing components are not silently imputed or reweighted.
- The combined score is a dimensionless factor exposure, not a forecast,
  return, investment instruction, alpha certificate or live ranking.
- Its own dated Rank IC and stability are computed; **not** a portfolio P&L
  or turnover/slippage/capacity estimate.

`--source hf` and `--source sqlite` are fully separated.
HF rows default to >=60 5-minute slots and >=85% original minute-source
coverage at signal/entry/exit; SQLite has no intraday fields and cannot
be awarded minute coverage. Missing months are never fabricated. This
quality-eligible universe is a selected subset, not an exchange-certified
historical tradable universe.

## What is STILL NOT validated

Even a high composite IC is not proof of future profitability.
Unresolved blockers: split/dividend adjustment, ticker recycling and
security identity, delisting/survivorship bias, genuine dated stock
eligibility, classifications' provenance, source redistribution rights,
real execution costs, sector exposure and independent unseen validation.

All V3 outputs retain:

```json
{
  "research_only": true,
  "point_in_time_validated": false,
  "approved_for_model_training": false,
  "live_execution_enabled": false
}
```
