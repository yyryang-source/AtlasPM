# Adaptive learning V3 (synthetic research)

Branch builds on V2 PR #71, whose local reported baseline is 726 passed tests
and five successful GitHub workflow runs. New code does not grant training
admission or live trading permission.

## Design

- Existing purged fold iterator now supports a fixed rolling training window
  without changing expanding defaults. Compact folds reuse a bounded index
  view, rather than allocating copies of all training indices.
- Each predeclared profile has a model, training window and refit cadence.
  A fixed expanding Ridge reference and historical mean are included. All
  profiles use the same observed feature sequence and prediction dates;
  incompatible label streams fail closed.
- Existing ensemble algorithm gets optional reference fallback, mature-only
  regime weighting, minimum improvement and drift quarantine. During warmup
  only the reference is used. Quarantine is reversible: models remain in the
  research competition, and may recover as matured performance improves.
  This is not permanent retirement or broker/paper promotion.
- `adaptive_research` freezes the policy configuration and reserves a new
  holdout in the same local research registry, then evaluates the policy
  using existing lagged economic metrics and paired block bootstrap against
  expanding Ridge, equal weight and momentum. It cannot self-promote to V2
  paper decision eligibility. No parameter tuning on the frozen holdout.
- CPU by default in uncalibrated auto mode, because measured 32-asset,
  40-session training favored CPU (1.51 seconds vs 27.20 seconds on RX 7600).
  Explicit cuda is supported; auto may choose GPU only when the operator
  marks the relevant workload calibrated. Batch sizing caps float32 input
  tensor bytes, not total activation/optimizer/runtime VRAM. No silent OOM
  fallback, GPU deterministic guarantee or mixed precision change.

## Run tests in the existing review worktree (Windows)

```powershell
git fetch origin phase5/adaptive-learning-v3
git switch --detach origin/phase5/adaptive-learning-v3
$py = ".\.venv\Scripts\python.exe"
& $py -m unittest discover -s tests -p "test_training_adaptive.py" -v
& $py -m unittest discover -s tests -p "test_training*.py" -v
& $py -m unittest discover -s tests
& $py scripts/maintenance/audit_source.py
```

Optional synthetic demo (new local registry only, refuses reusing holdout):

```powershell
@'
from atlaspm.research.training_benchmark import sessions
from atlaspm.research.training_adaptive import adaptive_research
import json
p = dict(mode="synthetic", synthetic_fixture=True,
         feature_names=["momentum","variation"], sessions=list(sessions(120,32)))
r = adaptive_research(p, registry_path="local_data/v3_demo.sqlite3",
                      holdout_start=75, momentum_feature="momentum")
print(json.dumps({k:v for k,v in r.items() if k!="weight_history"},indent=2))
'@ | & $py -
```

CLI: python -m atlaspm.research.training_adaptive --file local_data/v3.json
where JSON has payload, registry_path, holdout_start, momentum_feature and
optional arguments from adaptive_research's signature. To include a deep
profile specify sequence_length>=2 and a profile such as
{"name":"gru_medium","model":"gru","window":60,"cadence":10,"alpha":1.0}.
The same sequence length applies to every profile for alignment.

## Remaining work

Profile streams are retained in memory; no full-market streaming adaptive
controller, automatic hyperparameter discovery, verified real-data adapter,
automatic checkpoint recovery or full VRAM budgeting. Training cadence
and window are adaptive through weights across fixed candidates, not
arbitrary online hyperparameter mutation. The policy remains separately
evaluated, not integrated into paper execution until independent evidence.
The economic proxy still predicts t-to-t+1 while measuring lagged execution
t+1-to-t+2; its success must not be interpreted as calibrated executable
alpha. No actual market returns or financial projections are asserted.
