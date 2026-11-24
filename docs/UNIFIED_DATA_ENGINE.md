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

## Release blockers

- Independent data rights and source authenticity, exact historical release
  availability, venue calendars, survivorship/IPO/delisting/identity coverage,
  complete corporate actions, price adjustment semantics and independent
  price reconciliation are not yet certified.
- PR #89 real dataset bridge, #90 evidence additions, #91 safety/relocation,
  #92 lifecycle, #74 cumulative research and #66/#67 algorithm integration
  require current-base review and CI before main merge.
- True end-to-end real-data panel integration, controlled automatic downloads,
  trusted immutable version baselines, global resource limits and full
  dependency-safe legacy cleanup are pending. No raw data or compatibility
  modules removed by this PR.
- Full Linux/Windows CI and local real-data performance validation required.

## Windows acceptance and performance

Use the original existing virtual environment and review worktree. Do not
interrupt current raw downloads or run maintenance concurrently.

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM-data-review
git fetch origin
git switch --detach origin/integration/unified-data-engine
$py = "C:\Users\Kevin\Desktop\AtlasPM\.venv\Scripts\python.exe"
$env:PYTHONPATH = (Join-Path (Get-Location) "src")
& $py -c "import duckdb,pandas_market_calendars"
if ($LASTEXITCODE -ne 0) { throw "Missing lake/calendar dependencies" }
& $py -m unittest discover -s tests -p "test_unified*.py" -v
if ($LASTEXITCODE -ne 0) { throw "Unified tests failed" }
& $py scripts/maintenance/audit_source.py
if ($LASTEXITCODE -ne 0) { throw "Source audit failed" }
& $py -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw "Regression failed" }
```

For real-data read-only measurement, run the review code from the original
data directory only on stable completed partitions. Use identical month,
columns, symbol selection, thread count, batch size and cache conditions
for comparisons. Benchmark reports rows, wall time, CPU time, throughput,
Python allocation peak and OS process lifetime peak RSS (Windows working
set / Linux or macOS resource usage). Peak RSS includes interpreter/native
allocations and is not the query's isolated memory usage. No measured
performance improvement is claimed until actual reports are available.
