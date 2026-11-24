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
- `simulate`: in-process synthetic adapter to Unified Portfolio Engine, institutional
  risk checks and Paper OMS. Equal-weight only, exact universe, existing holdings;
  validates integration contracts but is not alpha-weighted execution.

## Usage

Install `.[portfolio]`; optional models use `.[research-ml]` / `.[research-deep]`.
Run `python -m atlaspm.research.autonomous_alpha --file local_data/alpha_request.json`.
Request keys: payload (existing training synthetic panel schema), validation_start,
holdout_start, optional models, cost_bps, minimum_sessions, max_drawdown,
sequence_length, seed, artifact_path and registry_path (ignored local_data SQLite).
Optional portfolio uses the existing synthetic Unified Portfolio Engine schema.
The output may be NO_TRADE; this is expected when evidence is insufficient.
Scores are ordinal research ranks, not expected returns or probabilities.

## Validation and limitations

Only one-session horizon is supported in this orchestration. Validation and
holdout use disjoint dates; the boundary-maturing validation label is excluded.
Models may refit on matured past observations within holdout; hyperparameters
and candidate identity are fixed. The benchmark is an equal-weight same-universe
return proxy; evaluation uses previous close ranks for the next close-to-close
interval, costs are simplified and actual execution is not implied. Missing
paths invalidate evaluation days. Synthetic data is bounded to 200,000 rows.
Annualization assumes 252 sessions; generated fixtures are not exchange calendars.
Bootstrap results are descriptive, not proof of statistical or economic alpha.

Open work: Factor V3 DuckDB integration with independent point-in-time evidence,
industry neutralization and missing factor handling in this orchestrator,
multiple horizons/decay and capacity, nested hyperparameter tuning and untouched
final holdout governance, regime uncertainty/liquidity/crisis validation, calibrated
forecasts, persistent lifecycle transitions and rollback, model ensemble snapshot
inference, portfolio optimizer alpha sizing, end-to-end research feedback,
real-data identity/corporate actions/survivorship validation, reproducible
full-scale resource benchmarks. RegimeNet and legacy screeners are not yet wired
to this entry point. No real data, weights, account information or orders go to Git.
