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
