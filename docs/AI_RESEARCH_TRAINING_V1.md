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

Opt in to `"model_artifact_path": "local_data/models.sqlite3"` in a training config. Ridge, historical mean, LSTM, GRU and Transformer save fold-specific numeric weights and training-only normalization, content-addressed by SHA-256. Other estimators fail explicitly if snapshot storage is requested. This does not use pickle or load executable objects. The same checkpoint and artifact database paths are required when resuming an artifact-enabled run. Snapshots from an interrupted run may exist; only a completed report establishes successful training. They do not authorize real-data inference or deployment.

```powershell
python -m atlaspm.research.training_artifacts --db local_data/models.sqlite3 list
python -m atlaspm.research.training_artifacts --db local_data/models.sqlite3 show ARTIFACT_ID
python -m atlaspm.research.training_artifacts --db local_data/models.sqlite3 predict ARTIFACT_ID --file local_data/synthetic_window.json
```

Inference accepts only a validated synthetic window of exactly the saved sequence length, with the original feature order and no earlier date than the artifact's first test session. CPU replay of GPU-trained weights may differ within numerical precision; dependency versions must match. No epoch resume or third-party estimator snapshots are provided yet.
