# AI Research Training V1

Standalone synthetic research foundation for cross-sectional forward close-to-close returns.
This complements existing stock rankers and risk-model tournament; it does not replace them.
Input: mode synthetic, synthetic_fixture true, feature_names, ordered sessions containing
date, timezone-aware close_at and assets with SYN: security_id, source synthetic, observed_at,
available_at, positive close and numeric feature dictionary. The timestamps are fixture claims,
not independent verification of real historical provenance.

Label horizons: 1, 5, 10, 21, 63 supplied sessions. Missing intervening security marks exclude
the label and increment the missing-path count; no delisting or corporate-action handling.
Expanding chronological folds group by session, purge labels overlapping test starts, and
apply a pre-test gap in supplied sessions. Fold-local standardization and fixed-alpha ridge
are compared against the training mean. Output includes MAE, RMSE, daily Pearson IC and
Spearman rank IC, fold coefficients, scaling, dataset fingerprint and out-of-sample predictions.

Run: python -m atlaspm.research.training_engine --file <synthetic-config.json>
Config contains payload and optional horizon, minimum_train_sessions, test_sessions,
embargo_sessions, ridge_alpha. Tests: python -m unittest discover -s tests -p "test_training_engine.py" -v
Install optional dependencies with pip install -e ".[portfolio]".

No hyperparameter selection, final sealed holdout, realistic transaction returns, actual
exchange calendar, data lake adapter, boosting/deep learning, persistent model registry or
training admission in this version. All real-data and live-execution flags remain false.

## V2 tabular model adapters
Install `pip install -e ".[portfolio,research-ml]"`. Optional models argument is a list from historical_mean, ridge, elastic_net, random_forest, lightgbm, xgboost, catboost, mlp. Default remains the original two baselines. All models share identical purged folds and fold-local preprocessing; optional estimators standardize targets using training labels only. Fixed hyperparameters and seeds, CPU single-thread settings where supported, dependency versions and model settings recorded. No automatic model selection, final holdout, sequence networks, persisted weights or deployment approval. Dedicated CI installs all eight adapters and tests aligned predictions and future perturbation invariance.


## Integrated low-memory research path

V3–V7 add LSTM/GRU/Transformer, chronological adaptive ensemble,
bounded hyperparameter search, experiment registry, fold checkpoints,
optional CUDA AMP, scoped memmaps and disk prediction archives.
The research-only permission flags remain unchanged.

Install base and IO dependencies:
```
python -m pip install -e ".[portfolio,research-io]"
```
For optional tabular models add research-ml; for PyTorch use research-deep.
Choose a CUDA-compatible PyTorch build locally if GPU execution is needed.
No real historical factor lake is admitted by these interfaces.

### Parquet streaming configuration

Pass a small JSON config to the existing CLI:
```json
{
  "root": "local_data/synthetic_input",
  "manifest": {
    "mode": "synthetic",
    "synthetic_fixture": true,
    "feature_names": ["momentum", "variation"],
    "partitions": [
      {"path": "part-000.parquet", "sha256": "REPLACE_WITH_ACTUAL_SHA256"}
    ]
  },
  "feature_cache_dir": "local_data/feature_cache",
  "array_budget_mib": 256,
  "spill_dir": "local_data/training_cache",
  "prediction_archive_path": "local_data/predictions/run001.sqlite3",
  "checkpoint_path": "local_data/checkpoints.sqlite3",
  "minimum_train_sessions": 10,
  "test_sessions": 5,
  "horizon": 5,
  "models": ["ridge", "historical_mean"],
  "ensemble": {"minimum_dates": 1}
}
```
Each input row has string date, timezone-aware close_at, security_id starting
with SYN:, source synthetic, observed_at, available_at, positive close,
and one numeric column for each feature. Partitions must be chronological,
and one date may span adjacent files. Files must remain unchanged.
Hashes are verified before and after reading; never consume results from
an input stream that has not completed successfully.

```
python -m atlaspm.research.training_engine --file local_data/stream_config.json
```

The raw session buffer is bounded by horizon + sequence_length. Compact
security/session/label metadata and valid row indices remain in RAM;
feature values and optional historical sequence observations use temporary
disk storage. Model fold arrays may spill to disk; native libraries,
OS file cache, linear algebra, and estimator internals are not governed
by the managed array budget. No fixed whole-process memory guarantee.
Feature caches are temporary, not reusable persistent feature snapshots.
Prediction archives cannot be overwritten: use a fresh archive path on rerun;
the fold checkpoint database may be reused for an identical run identity.
Only COMPLETE prediction archives represent completed runs.

### Reproducible memory benchmark

```
python -m atlaspm.research.training_benchmark --days 60 --assets 1000 --array-budget-mib 32
```
The benchmark starts isolated processes, measures OS-reported peak working
set/RSS and elapsed time including fixture generation and imports, and compares
metrics. This is a synthetic benchmark, not a market backtest or throughput
guarantee. No external data is downloaded.

## Numeric model snapshots

Opt in to `"model_artifact_path": "local_data/models.sqlite3"` in a training config. All eleven model adapters save fold-specific weights and training-only normalization, content-addressed by SHA-256. Scikit-learn linear, MLP and forest models use explicit numeric representations; LightGBM, XGBoost and CatBoost use their native model formats. This does not use pickle or load executable objects. The same checkpoint and artifact database paths are required when resuming an artifact-enabled run. Snapshots from an interrupted run may exist; only a completed report establishes successful training. They do not authorize real-data inference or deployment.

```powershell
python -m atlaspm.research.training_artifacts --db local_data/models.sqlite3 list
python -m atlaspm.research.training_artifacts --db local_data/models.sqlite3 show ARTIFACT_ID
python -m atlaspm.research.training_artifacts --db local_data/models.sqlite3 predict ARTIFACT_ID --file local_data/synthetic_window.json
```

Inference accepts only a validated synthetic window of exactly the saved sequence length, with the original feature order and no earlier date than the artifact's first test session. CPU replay of GPU-trained weights may differ within numerical precision; dependency versions must match. Epoch resume is independent of model snapshots.


### Epoch-level recovery

For LSTM/GRU/Transformer, set `"epoch_checkpoint": true` and
`"checkpoint_path": "local_data/checkpoints.sqlite3"`. The latest completed
epoch is saved atomically per fold and model, including optimizer, scaler,
batch generator and RNG state. The same dataset, code, dependency versions,
device and training configuration must be used. The loader uses PyTorch's
restricted weights-only deserialization for local checkpoints; do not import
untrusted databases. Interrupted partial epochs restart from the preceding
completed epoch. CPU replay is tested; CUDA reproducibility is not guaranteed.

### Maturity-aware monitoring

Set `"monitoring": {"window_dates": 30, "minimum_dates": 10, "coverage": 0.9, "baseline": "historical_mean"}` to produce chronological rolling MAE, reference-window drift alerts, residual-based interval half-widths and a research-only champion comparison. Only label blocks whose final maturity is strictly before the prediction date enter calibration and selection. The baseline must be included among predictions. Daily maximum absolute residuals calibrate a cross-sectional band; coverage is descriptive and is not guaranteed under dependence or regime changes. No automatic model admission or execution is enabled.

### Market-state-conditioned experts

Opt in with `"regime_ensemble": {"market": {"lookback": 20, "trend_threshold": 0.02, "volatility_threshold": 0.25}, "weights": {"minimum_dates": 5, "window_dates": 20}}`. A synthetic equal-weight shared-security close proxy classifies warmup, uptrend, downtrend, sideways and high volatility. The ensemble reuses existing model experts, calibrates each state's mixture from matured errors in that state, and falls back to global weights until sufficient history exists. No future prices enter the regime classifier. This is a regime-conditioned mixture, not independently fitted regime-specific models or a validated market index.

### Incremental expanding-window Ridge

Use `"incremental_ridge": true` with `"models": ["ridge", "historical_mean"]`. The training engine updates centered covariance and target cross-moments only for newly admitted matured rows, then solves the same fold-local standardized Ridge objective without retaining a full training matrix. This is an expanding-window sufficient-statistics update, not incremental neural/tree fitting. Replaying completed fold checkpoints reconstructs the moments in chronological order. Floating-point differences from full refits are tested within numerical tolerance. The feature matrix and label availability rules are unchanged.

### Research lifecycle review

Monitoring emits model_review statuses: REFERENCE_BASELINE, INSUFFICIENT_EVIDENCE, CANDIDATE_FOR_INDEPENDENT_REVIEW, RETAIN_AS_CHALLENGER and REVIEW_FOR_DEGRADATION. These are review-queue classifications, not actual deployment or destructive model retirement. Independent point-in-time data and holdout admission gates remain mandatory. Archived model snapshots remain immutable for reproducibility.

All lifecycle options may be combined for ridge/historical mean, including disk prediction archives, model snapshots, incremental refits, regime ensembles and monitoring. Deep estimators use epoch_checkpoint instead of incremental_ridge. Real data and broker permissions remain disabled.

### Runtime efficiency

Training only the historical mean or sequence models skips unused tabular
feature matrices and Ridge solves. When Ridge is not selected, the fold audit
may contain null coefficients; when tabular preprocessing is not needed, its
mean/scale are null as well. Sequence-specific scaling remains in model settings.
Prediction metadata is read without copying feature vectors. Prediction archive
readers use bounded keyset pages after the requested model has finished writing,
so appending other models does not pin a full-run SQLite WAL read transaction.

All model snapshots retain the panel's trailing-window eligibility length,
including tabular estimators that consume only the final feature row. Ensemble
and monitoring share one aligned chronological forecast validation contract.


## V6 real research dataset bridge (not approved for training)

The existing synthetic trainer, streaming loader, fold engine, models,
checkpoint and artifact code remain unchanged. V6 builds separate
real-unverified-research Parquet samples from the HF RTH factor mart,
without converting tickers to synthetic or certified security IDs.

~~~powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m atlaspm.cli market-db training-dataset-v6 --start-month 2020-01 --end-month 2020-06 --plan-only
& $py -m atlaspm.cli market-db training-dataset-v6 --start-month 2020-01 --end-month 2020-06 --horizons 1 5 21 --memory 2GB --threads 2
~~~

Build missing factor partitions separately using the existing factor CLI.
The plan does not run the calendar or read prices. Materialization checks
factor manifests, calendar membership, positive prices, unique keys and
last observed full-slot availability. A historical partial final bucket
or non-NYSE date blocks the partition; do not silently repair it.
Underlying factor derivation and vendor availability remain unverified.

Labels use observed close-to-close price change at exact NYSE session
offsets 1, 5, 10, 21 or 63. Missing intermediate sessions and end-of-range
censoring produce null labels; features with missing or nonfinite values
remain in the research dataset with features_complete=false. These are
not trade-executable or total-return labels, and ticker reuse, unverified
corporate actions and delisting missingness can materially bias them.
The close timestamp is only an earliest assumed feature availability time,
not independently proven vendor delivery. No sample is eligible for
formal model training.

Each month reads only the signal month and the necessary future months,
under the existing bounded DuckDB memory configuration. Content-addressed
registry IDs bind the selected configuration, calendar digest and input
file hashes; cached output partitions are verified before reuse.
The manifest includes counts and expanding-window folds with strict
label-end purge and embargo. Folds are calendar plans, not proof that
every fold contains sufficient valid samples. A short range may have no
folds. No model fitting takes place.

Registry output: local_data/training/v6/<dataset ID prefix>/manifest.json,
monthly Parquet partitions, and local_data/training/v6/latest.json.
Use the full dataset ID with training-dataset-inspect-v6 --dataset-id
to check registry identity and output hashes. Hashes establish local
integrity, not source authenticity; the manifest is not signed.
Run one writer at a time. Source data stays local.

V5 historical identity, action completeness, price adjustment, rights,
and feature availability gates remain unresolved. Synthetic trainer
validation rejects V6 manifests. Do not relabel the data to bypass
the guard. Future certified sample adapters must enforce the actual
evidence and time-aware security identity rather than trusting boolean
flags in a user-editable manifest.


## V6.1 real research preflight

Run after a successful V6 materialization:

~~~powershell
$m = Get-Content local_data/training/v6/latest.json -Raw | ConvertFrom-Json
& $py -m atlaspm.cli market-db training-preflight-v61 --dataset-id $m.dataset_id --minimum-train-rows 1000 --minimum-test-rows 100
~~~

Preflight verifies registry and partition hashes, recomputes the calendar,
validates keys, price and feature completeness flags, observed bar timing,
label end offsets and independently recalculates labels using exact
calendar-range row counts and terminal prices. Monthly windows are
bounded to the relevant future months. It then counts actual
feature-complete labeled training and test rows in each purged fold,
and retains prediction candidates with unavailable labels in the test
coverage statistics. Empty dates and insufficient folds are reported.
It rebuilds the fold plan from configuration rather than trusting stored
fold counts. The default minimum row counts are diagnostic thresholds,
not statistical validity claims.

Output: preflight.json beside the dataset manifest. No model fitting,
automated model selection, training approval or trade permission occurs.
The checks detect local structural inconsistencies, not true source
authenticity, historical identity, independent action completeness,
data-use rights or actual publication delays. Price-path labels remain
unadjusted-semantics-unverified research observations. Model promotion
must remain blocked until external certification is complete.


## V6.2 unified synthetic model tournament

The existing model zoo already contains ridge, historical mean, ElasticNet,
random forest, LightGBM, XGBoost, CatBoost, MLP, LSTM, GRU and Transformer.
The tournament reuses their existing implementations instead of adding
duplicate estimators. Run:

~~~powershell
& $py -m atlaspm.research.training_tournament --file local_data/tournament.json
~~~

The JSON request has payload (existing synthetic panel contract), config
(shared horizon, fold settings, sequence length, seed and resource options),
candidates (2–32 objects with model and optional ridge_alpha or
sequence_epochs), holdout_start (zero-based session index on a refit
boundary) and optional selection_metric (mae, rmse, mean_daily_rank_ic
or mean_daily_ic). Use identical shared horizon, sequence length and
fold settings for every candidate. Missing optional model dependencies
fail explicitly rather than silently changing the candidate set.

Candidates are trained on only the pre-holdout prefix, with identical
validation prediction keys and actuals checked by SHA-256. Undefined
metrics are ineligible; ties use input order. Only the winner is run
on the complete panel; its holdout uses rolling refits with matured
past labels. Holdout scores are not used to select a model. Candidate
predictions are discarded after scoring; per-trial metrics and dependency
versions are retained. This is synthetic research only and does not
demonstrate actual market alpha or authorize real model training.
The existing fixed-model experiment, model artifact registry and
ensemble implementations remain intact.


## V6.3–V6.5 unified synthetic search and lifecycle

Use training_autonomy with a single JSON request:

~~~powershell
& $py -m atlaspm.research.training_autonomy --file local_data/autonomy.json
~~~

Example request structure (payload is the existing explicitly synthetic
panel object, not a real-data V6 manifest):

~~~json
{
  "config": {"horizon": 5, "minimum_train_sessions": 20,
             "test_sessions": 5, "embargo_sessions": 1},
  "spaces": [
    {"model": "historical_mean", "grid": {}},
    {"model": "ridge", "grid": {"ridge_alpha": [0.1, 1, 10]}},
    {"model": "random_forest", "grid": {"n_estimators": [32, 64],
                                       "max_depth": [3, 5]}}
  ],
  "holdout_start": 46,
  "budget": 8,
  "search_seed": 42,
  "selection_metric": "mean_daily_rank_ic",
  "ensemble": {"window_dates": 20, "minimum_dates": 5},
  "regime_ensemble": {"market": {"lookback": 20}, "weights": {}},
  "monitoring": {"window_dates": 20, "minimum_dates": 5},
  "patience": 3,
  "cooldown_dates": 10
}
~~~

Include payload before execution, with enough sessions for the specified
holdout and label horizon. Grid keys features (list of feature-name
lists), ridge_alpha and sequence_epochs select explicit candidates;
other keys are model-specific bounded parameters from training_models.
The search is deterministic, model-balanced finite-grid sampling, at
most 4096 raw combinations and 32 evaluated candidates. It is not Bayesian
optimization. Feature selection is limited to explicit subsets, compared
using the same pre-holdout prediction universe. The seed is fixed, not
tuned. The estimator architecture and single-thread controls remain fixed.

The pipeline selects using prefix-only trials, then evaluates the selected
estimator and historical mean in one full-panel walk-forward pass. The
existing adaptive and optional regime-conditioned ensemble only use
matured errors. Monitoring and ensemble settings are fixed before the
holdout, not selected using holdout outcomes. The monitor reports coverage,
error drift and research champion candidates. Repeated drift can emit
review requests with patience and cooldown; it does not autonomously
change live models or execute a new drift-triggered fit. Scheduled
walk-forward refits are actual research fits. Existing incremental Ridge,
artifact and checkpoint APIs remain available separately.

The output keeps summaries, model configuration, selected features,
holdout metrics, weight history and retraining review requests rather
than duplicate prediction arrays. The V6 real dataset is still blocked
from this synthetic runner. Full certification, economic strategy
evaluation and deployment controls are separate prerequisites.


## Synthetic forecast-to-strategy closed loop (V7 integration)

Run the integrated model selection, portfolio policy competition and
next-open cash replay with:

~~~powershell
& $py -m atlaspm.research.strategy_closed_loop --file local_data/closed_loop.json
~~~

Request keys: payload (existing synthetic training panel); market (mode
synthetic, synthetic_fixture true, sectors mapping, sessions containing
date, open_at, close_at, source synthetic, open/close symbol-price
maps and optional volume); model_config; model_candidates (existing
tournament candidates); model_selection_end and strategy_holdout_start
(zero-based session indices); strategies (2–32 distinct dictionaries
with top_k, rebalance_sessions, cash_reserve and optional optimizer);
execution (initial_cash, optional fee_bps, slippage_bps,
settlement_sessions and execution_policy). Optional selection_metric,
signal_model (selected or adaptive_ensemble), ensemble and
max_validation_drawdown. Use the same explicitly synthetic fixed
universe of at most 20 names and identical closing marks in both
inputs. No externally supplied signals are accepted.

The three stages are model selection on the first prefix, strategy
competition on a subsequent validation interval and selected policy
only on the final holdout. Strategy trials use actual simulated net
returns and a validation drawdown cap, choosing maximum zero-risk-free
Sharpe among eligible trials. Cash starts fresh for every evaluation.
The chosen forecast is converted to close-available synthetic signals,
then executed at the next supplied open. Costs, settlement, integer
shares and rolling allocation use existing replay code. Empty forecast
sessions explicitly hold existing positions; this is necessary because
the trainer censors final horizon sessions. The original replay default
still rejects empty signals. A price-only fractional equal-weight
buy-and-hold benchmark is aligned to the evaluation window. No final
liquidation is assumed. The drawdown cap is a selection filter, not
a guaranteed future loss limit.

No candidate strategy uses holdout returns to select its parameters.
Only the selected policy runs on the final interval. A failed holdout
does not trigger a fallback search. The existing seven rolling allocation
methods may be requested subject to their input and feasibility checks.
This cash-only synthetic integration does not connect to the
institutional OMS, enable leverage or independently verify broker margin.
It demonstrates an offline research loop, not live autonomous trading
or real-data profitability. Real model training, verified identity,
corporate actions, data rights and deployment approval remain blocked.


## Closed-loop risk circuit breaker and hypothetical financing

Optional execution.risk_policy applies one fixed cash-only policy to
every strategy trial and the final holdout:

~~~json
{"max_drawdown": 0.20, "max_gross_leverage": 1.0,
 "equity_shock": 0.20, "max_stress_loss_fraction": 0.15}
~~~

Each synthetic close reuses the existing OMS marked-exposure risk
calculation. A breach permanently latches a halt for the run: no new
risk, sell-only targets at subsequent opens, with normal participation,
cost and settlement rules. Residual shares are retried; no same-close
or guaranteed liquidation is assumed. The halt and remaining positions
are explicit. Candidate strategies that halt during validation are
ineligible; a holdout halt is reported, not used to select another
strategy. The local close-risk journal reuses the OMS hash-chain helper;
this is not a signed broker audit or an integration with its actual
order journal. With no risk policy, previous replay behavior remains.

Optional top-level leverage_scenarios requests a separate static
financing envelope. Example scenario:

~~~json
{"shock": 0.20, "maintenance_fraction": 0.25,
 "initial_fraction": 0.50, "apr": 0.06, "days": 30,
 "min_excess_fraction": 0.10, "max_equity_loss": 0.50}
~~~

Starting equity is the assumed initial cash, not verified broker
net liquidation. The envelope solves simultaneous assumed initial
margin, stressed maintenance buffer, equity loss, financing APR
budget and gross ceiling constraints for a fully invested uniform
long-equity shock. If even 1x fails, no feasible fully-invested
ceiling is returned. It reports hypothetical principal, interest,
stressed equity and static zero-excess boundary per scenario.
The existing reverse-stress boundary helper is reused. These are
not IBKR margin requirements or liquidation prices. The report
does not modify simulated cash trades, permit borrowing, choose
leverage or transmit orders. Actual broker margin, order-bound
What-If, position identity and collateral need independent review.


## Consolidated synthetic acceptance and local OMS recovery

From the checkout root, with the existing portfolio and research extras:

~~~powershell
& $py -m atlaspm.research.training_acceptance
~~~

This runs a generated synthetic panel through model and strategy selection,
cash execution, the fixed risk policy and hypothetical financing. Separately
it runs the existing institutional synthetic OMS example with partial fills
and a duplicate event, persists its result and verifies idempotent retrieval.
The smoke checks actual outputs and fails on missing reconciliation,
audit-chain corruption or unexpected execution permission. The fixture
does not read real history or use the network. Temporary receipt files are
cleaned up. GitHub CI invokes the smoke on Windows and Linux. A smoke PASS
does not substitute for the full regression suite or certify real data.

To persist an explicit synthetic OMS simulation result locally:

~~~powershell
& $py -m atlaspm.execution.synthetic_oms_receipts --db local_data/paper/receipts.sqlite3 --request-id SYN:example-001 --file examples/unified_portfolio_synthetic.json
& $py -m atlaspm.execution.synthetic_oms_receipts --db local_data/paper/receipts.sqlite3 --request-id SYN:example-001
~~~

The same request ID and identical canonical payload return the committed
report; a changed payload under that ID fails. SQLite serializes the
local transaction. An interrupted uncommitted simulation can be repeated
because it has no external side effects. The result is checked against
the existing portfolio safety and reconciliation contract. This is a
local synthetic receipt, not a broker idempotency key or exactly-once
external execution mechanism. Local hashes are unsigned; a malicious
editor capable of rewriting both data and digests is outside this
integrity check. No new borrowing, real training or broker order route
is enabled. The institutional example and the historical cash replay
remain separate acceptance stages, not a continuous live order bridge.
