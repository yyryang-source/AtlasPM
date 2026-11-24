# Autonomous Quant Intelligence V2 — continuous synthetic replay

This review branch includes the tested V1 PR #68 and main maintenance PR #69.
Windows baseline supplied by the operator: 718 tests passed, no skips.
No real market training or live order authority is granted.

## What changed

- The existing in-memory purged trainer now accepts a strictly later
  `next_session` calendar date for horizon=1. The final observed feature
  cross-section receives predictions with `actual: null`; no future prices
  or synthetic future bars are supplied. Existing training modes are unchanged.
- `training_continuous.current_forecasts` requires a qualified V1 research
  record with the current source hash and exact frozen historical panel
  fingerprint. It refits the selected model and historical mean reference
  using only the observed prefix. The resulting streams are generated
  internally rather than supplied by the caller.
- `training_continuous.run_continuous` replays up to 60 post-holdout
  execution sessions using the existing decision policy, optimizer,
  institutional gate, Paper OMS and independent reconciliation. Signals
  are produced at t, executed with observed marks at t+1, matured at t+2.
  A one-session synthetic settlement rule moves prior receivables into
  cash without changing equity. Margin accrual uses elapsed calendar time,
  but unverified margin additions remain blocked. Current risk inputs use
  aligned historical returns, diagonal shrinkage and a variance floor.
  A drawdown cap can stop new simulated trades.
- One replay per research-run/account pair in the same registry. Interrupted
  replays remain consumed, not implicitly restarted from a different book.
- Research competition can include LSTM, GRU and Transformer via a shared
  trailing sequence length; device=auto resolves to a detected GPU or CPU.
  Fixed parameters and candidate budget remain in the recorded configuration.
  No GPU determinism or mixed precision guarantee is asserted.
- The factor preflight checks NYSE session-level coverage, invalid volume
  and extreme return counts; the results are diagnostics only. It still
  blocks real training regardless of structural or calendar success.

## Windows testing

Reuse the existing virtual environment with AMD PyTorch already installed.
In the review worktree, fetch and detach at this branch (not the data downloader):

```powershell
git fetch origin phase5/continuous-learning-v2
git switch --detach origin/phase5/continuous-learning-v2
$py = ".\.venv\Scripts\python.exe"
& $py -m unittest discover -s tests -p "test_training_continuous.py" -v
& $py -m unittest discover -s tests -p "test_training*.py" -v
& $py -m unittest discover -s tests
& $py scripts/maintenance/audit_source.py
```

Run the actual sequence trainer on the RX 7600 (float32, batch size 64).
Compare with the CPU using the same settings, not standalone GPU ops:

```powershell
& $py -m atlaspm.research.training_benchmark --deep-device cuda --days 40 --assets 32 --sequence-length 5 --epochs 2 --batch-size 64
& $py -m atlaspm.research.training_benchmark --deep-device cpu --days 40 --assets 32 --sequence-length 5 --epochs 2 --batch-size 64
```

The benchmark reports actual elapsed time, process peak RSS and allocated
device memory (not total reserved VRAM), separate metrics for all three
sequence models. GPU/CPU floating point equality is not guaranteed.

## Current limitations

- A fresh research registry is needed after code changes because the old
  study's source fingerprint intentionally fails qualification. Do not
  reuse a viewed holdout as a new independent test. Use synthetic fixtures
  in a separate development registry only.
- This is a bounded research replay, not a streaming live paper service.
  As-of forecasts refit from observed prefixes; end-to-end incremental
  candidate checkpointing, multi-session resume and disk streaming remain
  work items. Only calendar dates are read ahead.
- Existing OMS restrictions remain: cannot open an unheld ticker; after
  a name is fully sold, universe mismatch can cause NO_TRADE. No unsupported
  beta or named market scenarios are fabricated; constraints requiring those
  inputs fail closed. The separate runner drawdown cap is not a liquidation
  order. Real exchange settlement, corporate actions and liquidity remain
  unverified.
- Matured feedback changes risk scale through the existing policy, not
  automatic hyperparameter retuning. No claim of profitable strategies.
- Data identity, vendor availability, split/dividend/delisting and source
  rights evidence remain missing. Never copy raw data into the repository.

All research, paper and live permission flags remain false where required.
