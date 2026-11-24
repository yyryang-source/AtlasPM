# Repository maintenance and verification

Run from the checkout root:

```powershell
python scripts/maintenance/audit_source.py
python -m unittest discover -s tests -v
python -m atlaspm.research.training_benchmark --days 60 --assets 1000 --array-budget-mib 32
```

The source audit parses all Python files under src, tests and scripts, reports source
ownership by package, detects alias cycles/missing targets, and reports substantial
identical AST function bodies as candidates for review. It never imports the trading
modules, starts a download or deletes files. Syntax and alias errors fail CI.
Duplicate candidates are advisory: shared-looking financial calculations may have
different units, contracts or permission boundaries.

Keep root/quant/selection compatibility shims until their public import and patch
targets are retired. Keep old model baselines where used by comparison tests.
Avoid adding new copies of canonical domain implementations. Remove generated
artifacts only from ignored local workspaces, never from an active downloader.

The low-memory benchmark includes input construction and imported dependencies.
For small datasets, Arrow and SQLite overhead may increase peak RSS and latency;
the default in-memory route remains available. For large datasets, measure on the
actual hardware with the same model, date range and features. Disk mapping is
not a process-wide RSS limit. Do not infer trading performance or authorization
from code organization or benchmark success.
