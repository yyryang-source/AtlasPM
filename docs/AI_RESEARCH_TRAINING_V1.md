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
