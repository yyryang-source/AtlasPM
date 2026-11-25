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

## Read-only health and recovery rehearsal

From the checkout root, use `python -m atlaspm.framework.runtime_health health`.
The command reports queue freshness, visible/future pending work, quarantined
items, operator pause, lock presence and integrity failures. A missing queue
is not a healthy runtime. The check is a local observation, not provider or
broker availability. Locks are never automatically removed.

For a deliberate paused recovery rehearsal, stop using the existing
orchestrator operator control; after confirming no running writer use
`python -m atlaspm.framework.runtime_health backup snapshot-1`,
`python -m atlaspm.framework.runtime_health verify snapshot-1`, then
`python -m atlaspm.framework.runtime_health restore-isolated snapshot-1 restore-1`.
Copies are exclusive, checksummed and confined to local_data/runs/recovery;
current restricted-provider retention declarations are required. Neither
operation replaces the active queue, changes its pause state, copies broker
credentials or resumes any task. This covers the bounded research-event queue
only, not an account ledger, market lake, full database backup or disaster
recovery deployment. Keep copied records private under applicable retention
terms. Actual restoration of an active system needs a separate operator
review and a tested deployment-specific procedure.

## Market SQLite backup and isolated recovery

The sqlite_recovery module uses SQLite's online backup API for the canonical
local_data/atlaspm_market.sqlite3 file, including committed WAL contents.
Explicit operator retention review is required, with byte/time limits (up to
8 GiB and 30 minutes), disk check, standalone journal state, integrity_check
and SHA-256 manifest. Verification rechecks hashes around database inspection.

Only after checking applicable storage and backup rights, run:

    python -m atlaspm.data.sqlite_recovery backup market-1 --retention-reviewed
    python -m atlaspm.data.sqlite_recovery verify market-1 --retention-reviewed
    python -m atlaspm.data.sqlite_recovery restore-isolated market-1 rehearsal-1 --retention-reviewed

Copies stay under local_data/runs/sqlite_recovery. Use unique lowercase IDs.
Incomplete copies lack a valid seal; inspect and remove manually only after
confirming no process is using them. The active database is never replaced.
Time bounds are cooperative, not OS hard deadlines; disk space can change.
Checksums are not signatures or protection from a malicious local writer.
This covers the canonical market SQLite database only, not Parquet, other
registries, account ledgers or provider credentials. It is not full disaster
recovery or proof of provider rights.

## Installable package acceptance

CI builds a wheel and installs it into a fresh venv outside the checkout,
checks base dependencies and installed package origin, runs the architecture
contract and exercises recovery/current-research CLI help on Windows/Linux.
Build/base dependencies may be downloaded; this is not a pinned air-gapped
bundle. The integrated full-dependency gate uses 1300 passing tests.
