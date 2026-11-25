# Unified Data Engine — integration candidate

One local exploratory data access layer. Reuses the existing SQLite, DuckDB,
Parquet, SEC evidence ledger, factor mart and IBKR diagnostics. No new
warehouse, no raw duplication, no provider fabrication, no order submission.
Historical revisions and provider symbols are not independently certified.
All real training, formal backtest and live execution approvals remain false.

## Interfaces and data sources

| API | Existing source / behavior |
| --- | --- |
| iter_batches: equity_daily | SQLite adjusted daily close |
| iter_batches: equity_1m | HF raw monthly minute Parquet; strict raw QC |
| iter_batches: equity_5m | HF converted RTH monthly Parquet |
| iter_batches: option_1m / option_5m / option_daily | SQLite option_bars; distinct timeframe |
| iter_batches: option_quotes / option_greeks | SQLite timestamped tables |
| iter_batches: treasury_auctions / bond_quotes | SQLite |
| iter_batches: factors_hf / factors_sqlite | Existing source-separated factor mart |
| iter_batches: sec_filings | Read-only V5.2 candidate filing index; explicit CIK and prior filing date |
| iter_fundamentals | Existing annual snapshot CSV outputs with provenance |
| review_security | Existing V5.3 reviewed identity and corporate action snapshot |
| review_broker_capture | Existing IBKR private read-only capture/mapping diagnostic |
| parquet_manifest | Streaming SHA-256 of existing monthly files |
| archive_plan / audit_archive | No-write inventory / existing resumable QC |
| unified_pipeline.maintain | Offline conversion when missing, QC, factor build |
| research.data_admission.inspect_real_factors | Actual stored factor preflight; no fitting |

Explicitly unconnected: equity_realtime, macro, option_contracts, bond_terms.
No false empty tables. Live feeds, broader fixed-income terms and other
unconnected providers require separate source validation.

## Read contracts

```python
from contextlib import closing
from atlaspm.data.unified import iter_batches

with closing(iter_batches("equity_5m", start="2020-01-02", end="2020-01-02",
                          as_of="2020-01-02T15:00:00-05:00",
                          symbols=("AAPL",), columns=("close",),
                          purpose="exploratory", batch_size=2048)) as stream:
    for batch in stream:
        print(batch.columns, len(batch.rows))
```

Ranges are inclusive and capped at 367 calendar days. Intraday as_of requires
an offset-aware decision timestamp and uses existing NYSE calendar session
logic: nominal bar end must be <= decision time; incomplete boundary buckets,
weekends/holidays and after-early-close bars are excluded. The extra bar_end_ms
field is a nominal observation boundary, NOT verified vendor availability.
Without as_of, the existing exploratory RTH file interface is preserved.
Raw 1m uses existing raw QC, rejects invalid source OHLCV, misalignment and
RTH dates outside the declared month; selected duplicate keys fail. No invented
data or automatic adjustment. Raw QC requires additional scans.

SEC index uses explicit CIKs, all requested quarter inventory, and filed_date
strictly before the date-only cutoff. It contains candidate forms, not the
full EDGAR universe. Stored index digest and parser version are provenance,
not independent proof of completeness or actual acceptance time.
`iter_fundamentals` streams the two existing annual CSV output schemas,
requires prior snapshot date and provenance chronology, uses the screener's
ratio validator, and checks ordering and uniqueness. No forward fill.
`review_security` uses the existing latest-visible-revision ledger with
strict prior known_date, conflict resolution, evidence hash checks, listing
intervals and corporate actions. It materializes the visible ledger and is
not a bounded bar stream. Historical identity/source completeness remains
unverified; do not infer listing/exit events from absent bars.

All paths must be below gitignored local_data. SQLite query_only and read
transactions; SEC opens its existing DuckDB in read-only mode. Parquet
projection, date/ID predicates and bounded fetchmany batches use existing
DuckDB memory/spill configuration (default 256MB, one query thread).
Default batch 2048, cap 16384. File size/mtime hints are not content hashes
or immutable database snapshots. Discard results on any scan exception.
Always exhaust or explicitly close generators. Private broker review is
local-only (12 MiB per input), requires explicit fixture opt-in, and never
grants broker margin or order permissions. Do not share its full report.

## Incremental maintenance and research boundary

```powershell
python -m atlaspm.data.unified_pipeline plan --start-month 2000-02 --end-month 2000-03
python -m atlaspm.data.unified_pipeline maintain --start-month 2000-02 --end-month 2000-03 --execute
python -m atlaspm.data.unified_pipeline factor-preflight --start-month 2000-02 --end-month 2000-03
```

Plan is read-only. Execute is explicitly opt-in and requires a quiescent
single-writer workspace. No download and no raw deletion. Oldest first:
convert only missing 5m output from an existing raw file, reuse existing QC
checkpoints, stop on missing input or structural failure, then reuse factor
builder caches. Existing converted files are not silently replaced. Explicit
operator review/rebuild is required if raw data changed. The existing conversion
and QC helpers still use their own resource defaults; the memory flag here
controls factor building and reader scans, not all process allocations.
No independent downloader, no second checkpoint database.

Factor preflight scans existing factor structure, verifies metadata scope and
recorded row counts, checks current and historical daily lineage signatures,
streams SHA-256 of bar/daily/factor files and metadata including lookback
dependencies, and reports a deterministic evidence ID. In-process hash cache
avoids rehashing unchanged dependencies within a run. Hashes are evidence, not
signed certification; original builder lineage uses stat fingerprints, so
same-stat historic tampering is not independently excluded without a trusted
earlier digest. Missing or stale inputs block structural preflight. Source
files must remain quiescent. Warm-up null factors are permitted; null closes
are now rejected by the canonical factor audit without a second scan.

The existing training_dataset real-data rejection now calls the Research
admission boundary with the same ValueError contract. No user-provided flag,
local report or receipt grants real training. Research inspect_real_factors
is a diagnostic interface, not a model panel builder. Existing synthetic
training behavior remains unchanged.

## Final integration candidate (#94)

The final integration branch retains all eight specified PR histories, reconciles ledger read-only/integrity logic, Paper safeguards, 39 retired aliases, shared rolling labels, entry-lag semantics, model parameters and the new autonomous-alpha CLI. Combined CI and local data validation are required; branch inclusion is not acceptance.

The existing `market-db training-dataset-v6` CLI now calls the canonical `research.data_admission.build_research_dataset` entrypoint. It verifies factor lineage before and after construction and binds its evidence ID into the existing dataset manifest identity. `training-dataset-inspect-v6` can still inspect older manifests; `training-preflight-v61` now requires unified lineage, so old exploratory datasets must be rebuilt for this gate. The same V6 writer, partitions and exact-session label implementation are used. Preflight compares every source observation and requested feature against sample values, rejects modified inputs, rechecks labels and purged folds, and does not fit models. No new registry database.

`python -m atlaspm.data.quality_review --month 2000-02` checks each historical session's observed factor symbols against the reviewed ledger available strictly before that session. It counts missing/unknown memberships, unresolved actions and session gaps, hashes logical claim rows rather than SQLite file bytes, and can run the existing bounded price comparison with `--reference-csv local_data/reference.csv --reference-symbol AAPL`. This is a reviewer report, not proof of complete IPO/delisting, vendor publication times, rights or total-return data. No self-authored report can grant training.

## Release blockers

- Independent data rights and source authenticity, exact historical release
  availability, venue calendars, survivorship/IPO/delisting/identity coverage,
  complete corporate actions, price adjustment semantics and independent
  price reconciliation are not yet certified.
- All eight PRs are integrated into the candidate tree; their combined behavior requires current-head CI, code review and local acceptance before main merge.
- Certified real-data panel admission, controlled automatic downloads,
  independently trusted immutable baselines, and global resource limits remain pending. The candidate removes the 39 aliases already reviewed in #92; no raw data or historical benchmark implementations are removed.
- Full Linux/Windows CI and local real-data performance validation required.

## Windows acceptance and performance

Use the original existing virtual environment and review worktree. Do not
interrupt current raw downloads or run maintenance concurrently.

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM-data-review
git fetch origin
git switch --detach origin/integration/final-data-research
$py = "C:\Users\Kevin\Desktop\AtlasPM\.venv\Scripts\python.exe"
$env:PYTHONPATH = (Join-Path (Get-Location) "src")
& $py -c "import duckdb,pandas_market_calendars"
if ($LASTEXITCODE -ne 0) { throw "Missing lake/calendar dependencies" }
& $py -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency check failed" }
& $py scripts/maintenance/verify_release.py --minimum-passed 742
if ($LASTEXITCODE -ne 0) { throw "Full release gate failed" }
```

For real-data read-only measurement, run the review code from the original
data directory only on stable completed partitions. Use identical month,
columns, symbol selection, thread count, batch size and cache conditions
for comparisons. Benchmark reports rows, wall time, CPU time, throughput,
Python allocation peak and OS process lifetime peak RSS (Windows working
set / Linux or macOS resource usage). Peak RSS includes interpreter/native
allocations and is not the query's isolated memory usage. No measured
performance improvement is claimed until actual reports are available.


## Final acceptance audit — 2026-11-24

Baseline PR #94 at 70792ed: six workflows passed (Unified reader, selection,
factor database, portfolio, stock lake and historical replay); AI research
still running and Python checks queued at the latest baseline observation.
These are historical observations, not approval of subsequent commits.
The release remains draft and main unchanged. See current PR head CI.

Review corrections: 3153b1c validates synthetic metadata at trainer entry
and rejects caller-supplied prepared panels at public training entrypoints;
canonical preflight checks the same manifest before and after scanning,
checks factor lineage again and eliminates an extra manifest scan. V6
build/inspect/preflight explicitly deny formal training, formal backtest,
registry and broker order privileges. Python is not a security sandbox:
a caller rewriting the code or disguising actual observations as synthetic
is outside this API contract. c9f8a78 adds OS process I/O metrics and optional
untraced benchmark execution. 41bc6a7 adds an actual fixture subprocess
benchmark assertion, source file footprint for emitted batches and a
deterministic ID even for blocked quality reports.

### Source verification register

Public-source review on 2026-11-24:
- https://huggingface.co/datasets/mito0o852/OHLCV-1m states Finnhub origin,
  monthly Parquet and minute start timestamps. No stable listing ID, original
  publication/revision timestamps or independent proof of completeness are
  established by the card. Its hosted viewer reports incompatible file formats.
- https://finnhub.io/terms-of-service restricts redistribution, derived result
  sharing and business use without appropriate permission. A public mirror,
  a code license, and matching file hashes do not establish data-use rights.
  Obtain applicable written authorization/contract and retention terms before
  processing or sharing restricted data; no legal entitlement is inferred.
- SEC historical index and filing body evidence: historical membership/action
  coverage, accepted timestamps and source-independent corroboration remain
  unverified in local files. A filing date is not an intraday release time.
- Alpha Vantage listing observations: historical completeness, date semantics,
  access rights and independent corroboration remain unverified.
- Yahoo via unofficial yfinance: adjusted-close basis, revisions and use
  rights remain unverified; sample agreement is not independent certification.
- Massive option data: entitlement, contract lifecycle, timestamp availability,
  adjustment and quote completeness remain unverified.
- Treasury Fiscal Data and local bond/IBKR captures: actual source records,
  revisions, contract mappings, entitlements and capture freshness remain
  unverified for this release. Real-time equity, macro and other unconnected
  catalog entries remain unavailable through the unified reader.

No local archive, independent reference CSV, provider agreement, or real
reviewed ledger was available in this GitHub review environment. No actual
production data quality pass, licensing pass, PIT approval, or measured
production throughput is claimed. Do not share market data or derived
reports outside the authorized scope. No formal training/backtest or live
orders are approved.

### Call-chain and resource acceptance

market-db training-dataset-v6 -> research.data_admission.build_research_dataset
-> real_dataset_v6.build -> unified_pipeline.factor_preflight before/after
-> existing factor_db and Parquet -> existing V6 content-bound manifest.
market-db training-preflight-v61 -> data_admission.inspect_research_dataset
-> real_dataset_preflight_v61.run -> inspect / source and feature comparison /
label reconciliation / purged fold counts / final manifest and lineage check.
Actual feature availability, historical identifier completeness and total
return are not proven; the V6 labels remain exploratory close-to-close
symbol-based price returns. Synthetic JSON and Parquet trainers check the
existing metadata and reject externally supplied prepared panels. No
positive real-data admission path exists.

The older training_admission audit still serves source-specific calendar and
volume diagnostics, including SQLite. Do not delete it merely because some
checks overlap with unified factor preflight. Existing 66 shims and historical
benchmarks are retained until dependency and regression evidence supports
removal. Source audit identifies structural errors and exact duplicate-body
candidates; it is not proof that all semantically redundant code is absent.
Monthly historical quality review materializes the visible ledger per session;
hashing and final lineage checks intentionally trade I/O for correctness.
Measure before further optimization. DuckDB memory setting is not a hard
whole-process cap.

### Single local validation procedure

Run in the review checkout against the latest fetched #94 head. No new virtual
environment, downloads, order submission, or model training on actual data.
Software gate must pass with zero skips. If optional dependencies are absent,
install the same optional extras as the release CI using the existing
environment, then rerun the full gate; do not reduce the gate. Actual local
data steps require authorized access and a stable, completed archive. Use
one representative month first (2000-02 is an example, not verified coverage).
Keep all logs local and share only non-sensitive aggregate results as permitted.

```powershell
& {
    $ErrorActionPreference = "Stop"
    $root = "C:\Users\Kevin\Desktop\AtlasPM"
    $review = "C:\Users\Kevin\Desktop\AtlasPM-data-review"
    $py = Join-Path $root ".venv\Scripts\python.exe"
    $month = "2000-02"
    $authorizedLocalReview = $false # Set true only if the applicable data rights permit this use.
    Set-Location $review
    git fetch origin
    if ($LASTEXITCODE -ne 0) { throw "Fetch failed" }
    git switch --detach origin/integration/final-data-research
    if ($LASTEXITCODE -ne 0) { throw "Switch failed" }
    $env:PYTHONPATH = Join-Path $review "src"
    Remove-Item Env:ATLASPM_ALLOW_PAPER_ORDERS -ErrorAction SilentlyContinue
    $head = git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw "Git head failed" }
    & $py -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Dependency check failed" }
    $out = Join-Path $root ("local_data\release_validation\" + $head)
    New-Item -ItemType Directory -Force $out | Out-Null
    & $py scripts/maintenance/verify_release.py --minimum-passed 742 > (Join-Path $out "software.json")
    if ($LASTEXITCODE -ne 0) { throw "Software release gate failed: $out" }
    Write-Host "SOFTWARE GATE PASSED (not data certification): $head" -ForegroundColor Green
    if (-not $authorizedLocalReview) {
        Write-Host "REAL DATA STAGE NOT RUN: authorization not confirmed"
        return
    }
    Set-Location $root
    $first = "$month-01"
    $last = (Get-Date -Date $first).AddMonths(1).AddDays(-1).ToString("yyyy-MM-dd")
    & $py -m atlaspm.data.unified_pipeline factor-preflight --start-month $month --end-month $month > (Join-Path $out "factor.json")
    $factorExit = $LASTEXITCODE
    & $py -m atlaspm.data.quality_review --month $month > (Join-Path $out "quality.json")
    $qualityExit = $LASTEXITCODE
    & $py (Join-Path $review "scripts/maintenance/benchmark_unified_data.py") --dataset equity_5m --start $first --end $last --column close --memory 256MB --threads 1 --no-tracemalloc > (Join-Path $out "benchmark.json")
    $benchmarkExit = $LASTEXITCODE
    [PSCustomObject]@{ Commit=$head; Software=$true; FactorExit=$factorExit; QualityExit=$qualityExit; BenchmarkExit=$benchmarkExit; DataCertified=$false; Reports=$out } | Format-List
}
```

A quality-review exit of zero means the report was emitted, not that the
data are certified. Windows I/O transfer counters include non-disk I/O;
Linux process accounting distinguishes logical and storage bytes. Neither
establishes device-level activity or peak spill space. Emitted source file
bytes are the footprint of files producing batches, not actual bytes read,
nor the whole archive. Use identical workloads/cache conditions and record
hardware before performance comparisons. Latest-head CI and actual local
results are still required before final release review. No merge based on
the old green workflows.


### Local real research data preparation

Inventory first (presence only, no downloads or writes):

~~~powershell
.\.venv\Scripts\python.exe -m atlaspm.research.data_admission inventory
~~~

Select a contiguous locally available range with sufficient calendar sessions
and warmup months. Plan without writes, then add `--execute` when the same
partitions are not being modified by another process:

~~~powershell
.\.venv\Scripts\python.exe -m atlaspm.research.data_admission prepare --start-month 2000-01 --end-month 2000-07 --warmup-months 2 --memory 256MB --threads 1
~~~

Dates above are examples, not observed local availability. Execution calls
existing offline conversion/QC/factor maintenance, V6 sample builder with
mandatory lineage, and exact feature-source/label/fold checks. Output
includes dataset ID, fold sample minima and missing-session blockers.
The 24-target-month bound and 2-month default warmup constrain the initial
preparation; this is not a complete 36-month factor-history guarantee.
Missing source months or impossible fold calendar stop before maintenance.
This entry point serializes its own writers; stop other direct builders or
downloaders modifying these inputs. A stale lock requires operator review.
No trainer or independent data/model approval is invoked.


The dataset preflight additionally reports strict closing-bar-path sample
counts per horizon and fold. An otherwise valid observed-price label is
excluded from those counts if any session in its h+1-session path lacks
a last bar ending exactly at the calendar session close (including early
closes). Original research partitions and observed-price labels are unchanged.
This is a conservative structural filter, not verification of completeness
within the session, historical identity, adjustments, vendor availability
or rights. The preparation pipeline reports a separate blocker if strict
fold sample minima fail. Aggregation reuses the existing label window pass;
no second sample warehouse or model fitting is introduced.


Canonical offline maintenance now requests exact NYSE closing-bar contiguous
factor windows (HF only). Missing exchange sessions, off-calendar days or
missing closing-bar observations reset the rolling segment. Momentum,
volatility, moving average and volume windows cannot bridge these boundaries.
Original daily observations remain intact. The factor cache records the
window policy and calendar hash; canonical preflight detects a changed
calendar. Standalone legacy factor builds remain available for exploratory
compatibility, and an already strict partition retains its policy by default.
Canonical maintenance requires the existing calendar extra. This does not
certify historical identity continuity, adjustments, intra-session coverage,
rights or feature availability. A changed policy rebuilds derived factor
partitions; no source bars are replaced.


### Evidence-bound identity and action path review

After a lineage-verified dataset is prepared, an optional separate preflight
joins each sample session to the existing date-precision evidence ledger.
Use the full dataset ID and an explicit review cutoff later than the last
session, not later than the current UTC date:

~~~powershell
.\.venv\Scripts\python.exe -m atlaspm.research.data_admission identity-review --dataset-id FULL_DATASET_ID --as-of YYYY-MM-DD
~~~

A counted path requires a prior-known identity on every session, agreement
with the retrospective resolved identity, one security ID throughout the
path and a complete observed closing-bar path. A visible split, dividend,
merger, ticker change or exit action on a path excludes it from the
action-free subset (including the signal session; non-session actions
are assigned to the next session). No adjusted or total return is invented.
The retrospective evidence is for exclusion diagnostics, never a predictive
feature. Existing claims can be incomplete, conflicting or retrospectively
revised; feature lookback identity and vendor adjustment semantics remain
unverified. No model permission or official data certification is issued.
The review uses temporary DuckDB rows and a separate identity preflight
report in the existing dataset directory, with a 550-session budget.


### Real fold-array handoff check (no model fitting)

The identity-review command can optionally materialize one canonical fold
using the existing scoped FoldArrays allocator:

~~~powershell
.\.venv\Scripts\python.exe -m atlaspm.research.data_admission identity-review --dataset-id FULL_DATASET_ID --as-of YYYY-MM-DD --matrix-horizon 1 --matrix-fold 0 --array-budget-mib 16
~~~

The horizon and zero-based fold index must exist in the dataset. The
closing-bar, stable-identity and visible-action exclusion predicate is
shared with the label count audit. Only rows in the canonical purged train
and test ranges are copied into a temporary DuckDB table; the feature
matrix and target vector are allocated separately and read in bounded
batches. Shapes, deterministic row digests and scoped array/spill byte
counts are reported; the arrays and spill files are discarded. NumPy
(existing native extra) is required. The array budget is not a total
process RAM limit. This is a retrospective labeled subset, not a
prediction universe or unbiased performance evaluation. Feature-lookback
identity, adjustments, rights and independent admission remain unverified.
No estimator, model artifact or registry promotion is invoked.


Identity review also counts a stricter retrospective feature-history
subset using the longest required existing factor lookback (21 prior
sessions for the default features, up to 252). Each required observed
session must have a closing bar, agreeing reviewed identity and no
visible action, in addition to the existing forward-label conditions.
Only sessions inside the dataset are reviewed, so early signals without
enough in-dataset history are conservatively excluded even when earlier
factor warmup files exist. The CLI fold-array check now requires this
stricter subset. Existing Python diagnostic callers may still request
the older forward-path-only matrix; neither subset authorizes training.
Feature values are not independently recomputed or adjusted, and no
source completeness or point-in-time certification is implied.
