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
