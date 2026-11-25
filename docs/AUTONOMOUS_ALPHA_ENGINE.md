# Autonomous Alpha Engine — incremental synthetic research implementation

This is an initial executable integration of the fourth development stage, not
a claim that V1–V3 acceptance is complete. All actual market data remains blocked
from training. No broker calls, trading privileges or production model admission.

## Architecture

- `research/alpha_factory.py`: bounded economic rule candidates; signed training-only
  IC weights, redundancy and direction consistency on synthetic cross-sections.
- `research/alpha_metrics.py`: common delayed one-session portfolio proxy,
  transaction costs, benchmark-relative and risk metrics, fixed cost stress,
  moving block bootstrap with conservative multiplicity adjustment.
- `research/autonomous_alpha.py`: reuse purged walk-forward training, optional
  model snapshots, adaptive and regime ensembles, close-observed market state,
  locked validation winner and separate holdout veto, research signals and
  existing local research registry. No candidate may replace a failed holdout.
- `alpha_lifecycle.py`: append-only version events with hash-chain checks,
  idempotent registration and explicit suspension/retirement. No paper promotion.
- `alpha_benchmark.py`: deterministic synthetic request generation and measured
  elapsed time/whole-process peak RSS using existing cross-platform helpers.
- `simulate`: in-process synthetic adapter to Unified Portfolio Engine, institutional
  risk checks and Paper OMS. Equal-weight only, exact universe, existing holdings;
  validates integration contracts but is not alpha-weighted execution.

## Usage

Install `.[portfolio]`; optional models use `.[research-ml]` / `.[research-deep]`.
Generate a synthetic request using
`python -m atlaspm.research.alpha_benchmark --request-output local_data/alpha_request.json`.
Run `python -m atlaspm.cli autonomous-alpha --file local_data/alpha_request.json`.
Benchmark in a fresh process: `python -m atlaspm.research.alpha_benchmark --days 65 --assets 32`.
Use a new output path on repeated request generation. No speedup is claimed.
Request keys: payload (existing training synthetic panel schema), validation_start,
holdout_start, optional models, cost_bps, minimum_sessions, max_drawdown,
sequence_length, seed, artifact_path and registry_path (ignored local_data SQLite).
Optional portfolio uses the existing synthetic Unified Portfolio Engine schema.
The output may be NO_TRADE; this is expected when evidence is insufficient.
Scores are ordinal research ranks, not expected returns or probabilities.

## Validation and limitations

Only one-session horizon is supported in this orchestration. Validation and
holdout use disjoint dates; the boundary-maturing validation label is excluded.
Candidate models receive only the validation prefix. After candidate identity
is locked, only that candidate (or its required ensemble constituents) is
trained/evaluated over the holdout. Models may refit on matured past observations
within holdout; hyperparameters and candidate identity are fixed. The benchmark is an equal-weight same-universe
return proxy; evaluation uses previous close ranks for the next close-to-close
interval, costs are simplified and actual execution is not implied. Missing
paths invalidate evaluation days. Synthetic data is bounded to 200,000 rows.
Annualization assumes 252 sessions; generated fixtures are not exchange calendars.
Bootstrap results are descriptive, not proof of statistical or economic alpha.

Open work: Factor V3 DuckDB integration with independent point-in-time evidence,
industry neutralization and missing factor handling in this orchestrator,
multiple horizons/decay and capacity, nested hyperparameter tuning and untouched
final holdout governance, regime uncertainty/liquidity/crisis validation, calibrated
forecasts, independent paper-admission transitions and active-version rollback, model ensemble snapshot
inference, portfolio optimizer alpha sizing, end-to-end research feedback,
real-data identity/corporate actions/survivorship validation, reproducible
full-scale resource benchmarks. RegimeNet and legacy screeners are not yet wired
to this entry point. No real data, weights, account information or orders go to Git.


## Multi-horizon research tournament (synthetic only)

The optional module `python -m atlaspm.research.alpha_multi_horizon --file local_data/multi_alpha.json`
reuses the existing V6.2 model tournament and purged trainer for 1, 5 and
21-session labels (other existing supported horizons can be selected).
Request fields: payload (synthetic panel), holdout_start (zero-based index),
optional horizons, config (tournament common options except horizon),
candidates (2–8) and selection_metric. All horizons must align to the same
refit boundary. Default test_sessions is 4; with minimum_train_sessions=10,
embargo_sessions=1 and sequence_length=1, index 60 aligns the defaults.
Ensure enough sessions remain after the boundary for the longest label.

Every horizon's candidate identity/configuration is locked using only the
prefix before any selected model receives full data. Only locked winners
refit on matured past labels. All horizons share the holdout start, but
their validation periods and matured label counts differ. Metrics are not
directly comparable across horizons. A deterministic fixed-spacing
nonoverlapping holdout diagnostic reports missing whole cross-sections,
rather than replacing missing dates with convenient later ones. It is a
prediction diagnostic, not a trading return series, Sharpe estimate or
claim of independent observations. No cross-horizon return compounding.

The report retains selection lock SHA-256, per-horizon trial metrics,
winner-only holdout metrics and cohort coverage, and releases full
prediction arrays after each horizon. Limits: 2,000 sessions, 200,000 asset
rows, 64 features, 8 candidates and 2,000 planned fold fits. These are
workload bounds, not a total RAM or runtime guarantee. No model promotion,
signals, portfolio handoff, real-data training or broker authorization.
The existing one-horizon Alpha entry point is unchanged.
