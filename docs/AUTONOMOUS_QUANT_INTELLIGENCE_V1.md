# Autonomous Quant Intelligence V1 — phase 5 initial review

Base main: 24455b097c5f0c1cf583970c033372034c0b8c12 (PR #65).
This branch implements an initial synthetic-only vertical slice. It is not
a production trading system or an approval to train on real market data.

## Audit and reuse
- Reuse training_engine, training_dataset (purged folds, embargo), ExpandingRidge,
  optional tabular estimators, training_registry, training_monitor and existing
  Unified Portfolio Engine / institutional gate / Paper OMS. No parallel optimizer.
- Existing Parquet trainer remains synthetic-only. Existing factor V1/V3 marts
  explicitly lack point-in-time, security identity, coverage and rights verification.
- Existing experiment selects minimum MAE; the new controller adds lagged net
  performance and rank IC, paired moving-block bootstrap and multiplicity control.
- Compatibility shims are intentionally retained; removal needs import migration evidence.

## Run on Windows PowerShell
From a separate checkout, not the directory running the historical download:

```powershell
python -m pip install -e ".[portfolio,research-ml,research-io,lake]"
python -m unittest discover -s tests -p "test_training_autonomous.py" -v
python -m unittest discover -s tests -p "test_training*.py" -v
python -m unittest discover -s tests -p "test_unified_portfolio_engine.py" -v
python -m atlaspm.research.training_benchmark --days 40 --assets 32 --array-budget-mib 1
```

To generate a synthetic study configuration in an ignored directory:

```powershell
New-Item -ItemType Directory -Force local_data | Out-Null
python -c "import sys,json; sys.path.insert(0,'tests'); from research.test_training_autonomous import fixture; from pathlib import Path; Path('local_data/phase5-study.json').write_text(json.dumps(dict(payload=fixture(),registry_path='local_data/phase5.sqlite3',holdout_start=62,momentum_feature='momentum',minimum_train_sessions=10,budget=4)),encoding='utf-8')"
python -m atlaspm.research.training_autonomous --file local_data/phase5-study.json
```

A failed or interrupted run consumes the reserved holdout. Do not delete
the registry or change its path to repeat the same holdout. This is a
local integrity boundary, not a tamper-proof holdout authority. Optional
model names are specified once in optional_models, not tuned manually.
Candidates use a fixed seed and bounded deterministic feature-family/ridge
configurations; no seed shopping. All comparisons use aligned eligibility.
Costs are a flat one-way notional bps proxy, including final liquidation;
signal at close t, entry close t+1, exit close t+2. Incomplete executable
paths fail, not silently omitted. Annualization is 252 synthetic sessions.

For an existing completed local factor partition only (never run a builder
or backfill from this branch):
```powershell
python -m atlaspm.research.training_admission --months 2000-02 2000-03 --source hf
```
The command hashes Parquet/manifest before and after structural audit and
always returns BLOCK_REAL_TRAINING. It does not alter the partition;
DuckDB may use its existing local temp workspace. Copy no raw data to GitHub.

## Paper decision and feedback
`python -m atlaspm.research.training_decision_policy --file local_data/paper.json`
accepts a JSON object with payload (existing synthetic Unified Portfolio input,
plus market_data_available_at and history_available_at), streams (selected
candidate model and historical_mean chronological forecast rows), registry_path,
run_id. Requires the holdout to precede the paper decision date. Unmatured
outcomes are redacted. Uses the existing uncertainty/drift monitor and
tightens gross exposure, never relaxes risk caps. Caller-supplied streams
are synthetic; not authenticated model artifacts. A no-trade result
preserves existing holdings, not an automatic liquidation to cash.

A reconciled OMS run gets a local paper_decision_id. To mature a synthetic
mark-to-market observation, call
`python -m atlaspm.research.training_decision_policy --feedback --file local_data/feedback.json`
with registry_path, decision_id, as_of, and observation containing mode,
synthetic_fixture, observed_at, available_at and marks (symbol -> positive
price, exact post-fill equity coverage). Only the recorded maturity date
is accepted; duplicate identical results are idempotent and revisions
are rejected. Receivables/payables are included, margin carrying cost is
estimated. Five prior losses stop trading; negative sum of at least three
recent mature results halves gross exposure. These are initial fixed
heuristics, not empirically optimized controls.

## Remaining acceptance gaps
- Actual historical security identity, corporate actions, delistings,
  vendor publication timestamps, exchange calendars and rights evidence;
  trusted data-admission attestations and audited real training adapter.
- Real liquidity/ADV-based capacity, market impact, and multi-regime,
  multi-horizon economic research. Flat costs are not capacity evidence.
- End-to-end streaming controller / candidate checkpoints and resource
  benchmarks on the actual Windows host. Existing trainer capabilities
  do not prove controller scalability.
- Artifact-authenticated predictions, as-of forecast generation without
  future data, automatic multi-session account/settlement progression,
  cash liquidation path, portfolio feedback-driven candidate research,
  and margin-specific paper policy beyond existing conservative gate.
- Independent validation of research thresholds, robust multiple-testing
  methodology across studies, and full Windows/Linux CI. No live permission.

No claim of improved real returns or measured speedup is made.
