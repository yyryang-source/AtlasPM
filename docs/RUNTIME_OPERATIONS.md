# Offline operations and release boundary

## Research workbench job integrity

Existing stock-screen and stock-backtest jobs now bind the input byte counts,
SHA-256 values and request options. The worker checks these before launch and
after successful completion, under one exclusive per-job lock. A completed
report has a recorded byte count and digest. Use research_jobs.verified_report
to verify it before consuming evidence. Older jobs without sealed receipts must
be rerun to obtain verified reports. Hashes are accidental corruption checks,
not signatures, authorization or defense against a malicious concurrent writer.
Input copies are streamed with a 2 GiB aggregate ceiling; reports are capped at
64 MiB when sealed, state at 64 KiB. The child process itself is still bounded
by the existing 30 minute timeout, not by an OS resource quota.

Interrupted or stale jobs are not automatically resumed. A remaining worker.lock
requires an operator to confirm that both worker and child have stopped before
removing the lock; never remove a lock based only on age. retry_job creates a
fresh run from a verified failed snapshot and never overwrites the original.
Unsealed, incomplete or altered inputs must be freshly submitted. No broker
commands are permitted by the research workbench. No new automatic trading
authority is provided.
