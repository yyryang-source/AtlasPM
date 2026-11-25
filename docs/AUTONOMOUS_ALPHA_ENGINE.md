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

## Consolidated synthetic rank-to-portfolio release acceptance

The multi-horizon research entry accepts optional replay_payload and
rank_weights together. The latter maps every selected horizon (string keys)
to a positive weight, locked before fits. The former is the existing cash
replay input with explicitly supplied holdout open/close/volume/sector data,
matching the training panel dates and close marks; each session has sectors
instead of caller signals. Fixed universe of 2–20 synthetic IDs, at most 250
holdout sessions, mandatory risk, execution and position lifecycle policies.
The bridge retains only percentile ranks from each locked winner. It does
not compare forecast magnitudes across horizons, use actual labels for
ranking, or choose weights after viewing the holdout. Incomplete cohorts
and ties at the selection boundary produce no new signal. The common window
ends before the longest label tail; later sessions remain in the replay for
risk management and settlement. No forced final exit or claimed profitability.
The existing replay optimizer, risk and execution code are reused. All real
training, model promotion, production and broker permissions remain false.

Run one consolidated Windows/Linux offline release gate from the root:
`python scripts/maintenance/verify_release.py --minimum-passed 1246 --integrated`.
It includes full optional-dependency tests, source/architecture checks and the
existing isolated event/research/OMS/multiasset/settlement offline fixture.
A paused orchestrator does not count as a passing integrated release. CI
uses the same gate. This is not certification of provider rights, independent
historical evidence, real-model efficacy or authenticated Paper execution.

### Integrated strategy and execution review

Optional strategy_review=true with replay_payload and rank_weights evaluates
three predeclared synthetic cases: locked model ranks, prior completed-close
momentum (same replay, optimizer and risk policy), and unchanged model ranks
with doubled fees/slippage/impact and half the participation cap. All cases
use the same full holdout dates and fixed universe; the momentum baseline
starts after the first observed holdout close. The fractional equal-weight
buy-and-hold reference is descriptive and frictionless. No winner selection.
The review reconciles per-symbol/day overnight and intraday P&L, modeled fees,
slippage and impact against executed orders, holdings, cash and daily NAV;
sector attribution requires unchanged declared sector assignments. No real
broker TCA or opportunity cost of unfilled orders is asserted. Stress outside
the execution model limits is rejected, never silently clipped.
Nominal Treasury replay optionally accepts curve_node_shocks_bps (numeric
maturity-year keys): linear interpolation of zero-rate shocks on the existing
flat fitted base and key-rate DV01. This is not a calibrated observed curve.

European option risk diagnostics are available through
atlaspm.strategies.options.option_risk.review_chain (existing OptionHistory,
explicit as-of, spot and availability, continuous rate and dividend yield).
It checks 1–128 contracts of one underlying, selects only fresh same-day
quotes, solves bounded bid/mid/ask implied vols and computes Greeks and
joint spot/volatility shock P&L. American options, missing quotes and
unidentified IVs remain explicitly blocked. No surface fitting or actual
margin calculation is implied.

This consolidated candidate uses a 1258-pass floor with --integrated on
Windows and Linux. Do not interpret passing synthetic tests as all-project
production acceptance. Independent historical source rights/identity/action
coverage, unconsumed real holdout efficacy, authenticated IBKR Paper lifecycle,
production observability, backup/recovery and operator authorization remain
separate release gates. Unsupported American/multi-leg option execution and
observed yield curve calibration are not claimed by these diagnostics.
