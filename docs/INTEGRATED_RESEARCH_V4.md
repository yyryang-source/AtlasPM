# Integrated research V4 and one-command release acceptance

This batch starts from V3 PR #72, with all five V3 GitHub workflows
successful, and the operator's 732-test run (last local OK line not supplied).
No main merge is performed by this branch. No real market or live authority.

## Development

1. `training_discovery.discover`: deterministic, bounded grammar of
   cross-sectional ranks, signed sums, differences and interactions.
   Tie-aware scores use only each observed session. Candidate trials are
   confined to a chronological validation prefix, screened for rank IC
   stability in three chronological blocks and cost-adjusted advantage
   over equal weight and momentum using multiplicity-adjusted paired
   block bootstrap. Redundancy to the current best is measured as mean
   absolute daily rank correlation. Only a single winning rule enters
   a fresh reserved holdout; never self-promote. Memory and search are
   bounded by explicit row, feature and candidate budgets. This is not
   symbolic regression with arbitrary expressions or portfolio capacity
   analysis.
2. Correct the economic label mismatch in an opt-in shared trainer path:
   `entry_lag_sessions=1, horizon=1` predicts close(t+2)/close(t+1)-1,
   with full two-session label maturity, purge and embargo. The same
   contract is implemented in JSON and streaming Parquet panel builders.
   Existing defaults stay unchanged. `adaptive_research` can use
   `execution_aligned_labels=True`; the strategy evaluator independently
   checks label values against simulated entry/exit marks.
3. One command for all regression tests, skipped-count enforcement,
   minimum passed count and source audit. CI includes a Windows Python
   3.14 all-extras CPU release job, alongside existing platform jobs.
   It cannot prove AMD driver-specific behavior.
4. No new live trading permissions, no raw data uploads, and no changes
   to downloader state. V1/V2 legacy forecasting contracts remain
   available but not automatically upgraded to the lagged target; V4
   discovery is not paper eligible and lagged as-of prediction is not
   yet supported. Real point-in-time admission, corporate actions,
   historical IDs, rights, capacity, and production paper service are
   still outstanding.

## Single local acceptance (only after cloud checks are green)

Use the existing review worktree, not the historical data downloader.
No need to reinstall torch or dependencies if already satisfied.

```powershell
git fetch origin phase5/integrated-research-v4
git switch --detach origin/phase5/integrated-research-v4
$py = ".\.venv\Scripts\python.exe"
& $py scripts/maintenance/verify_release.py --minimum-passed 742
```

CI baseline is 742 (732 plus ten new tests), strict zero skips and zero
failures. Before a real release inspect actual CI results, not estimates.

## Synthetic discovery demonstration (not real-market training)

Use a new local research registry for an independent synthetic study:

```powershell
@'
import json
from atlaspm.research.training_benchmark import sessions
from atlaspm.research.training_discovery import discover
p = dict(mode="synthetic", synthetic_fixture=True,
         feature_names=["momentum","variation"], sessions=list(sessions(120,32)))
r = discover(p, registry_path="local_data/discovery_v4_demo.sqlite3",
             holdout_start=70, momentum_feature="momentum", budget=10)
print(json.dumps(r,indent=2))
'@ | & $py -
```

Discovery may return no winner; that is valid research evidence, not a
software failure. CLI: python -m atlaspm.research.training_discovery --file
local_data/discovery.json, with payload and keyword arguments.
