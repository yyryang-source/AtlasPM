# Unified Data Engine — integration candidate

## Scope and release status

This is an exploratory read facade over existing SQLite and monthly Parquet
stores, not a new warehouse, downloader, security master, or training API.
No source is certified PIT; all batches have training, official backtest and
live execution approval set to false. Consumers must not interpret an
exploratory batch as a permission token. Existing CLI and raw data are unchanged.

| Dataset | Existing backend |
| --- | --- |
| equity_daily | market_db SQLite adjusted daily closes |
| equity_1m | HF raw monthly minute Parquet, nominal RTH, strict raw QC |
| equity_5m | existing HF RTH converted monthly Parquet |
| option_1m / option_5m / option_daily | market_db option_bars, separate timeframe |
| option_quotes / option_greeks | market_db timestamped tables |
| treasury_auctions / bond_quotes | market_db SQLite |
| factors_hf / factors_sqlite | factor_db source-isolated monthly Parquet |

Unconnected catalog entries deliberately raise DataUnavailable:
equity_realtime, macro, option_contracts, bond_terms.
Several have existing domain modules or raw storage, but adapters are not
implemented here. This is not a claim of provider availability. Never fill
these gaps with synthetic data or conflate two sources.

## Python API

```python
from contextlib import closing
from atlaspm.data.unified import iter_batches

with closing(iter_batches("equity_daily", start="2020-01-01", end="2020-01-31",
                          columns=("adjusted_close",), symbols=("AAPL",),
                          purpose="exploratory", batch_size=2048)) as stream:
    for batch in stream:
        print(batch.columns, len(batch.rows), batch.origin)
```

The range is inclusive, at most 367 calendar days per scan. Timestamp-only
quote/Greek date filters use UTC; trading-day columns use the existing source
calendar. Millisecond timestamps retain their original semantics (bar start,
not availability). Provider symbols and CUSIPs are source keys, not certified
historical permanent security IDs. Keys are always returned; SQLite rows also
return their recorded source. Parquet paths and size/mtime hints are provided;
these are not cryptographic content hashes, and SQLite WAL content is not
represented by the hint. Do not treat them as reproducible snapshot receipts.

SQLite opens in read-only URI mode with query_only and a read transaction.
DuckDB uses existing configured memory and spill paths and reads one monthly
partition at a time; projection and date/ID predicates are pushed into SQL.
Batch size is bounded (1–16384); this is not a process-wide RSS guarantee.
All monthly paths are checked before yielding. Empty results do not prove
coverage, and absent sessions are not synthesized. Selected rows are checked
for invalid keys, duplicate keys within a partition, nonfinite numeric values,
and basic price/volume constraints. This is partial structural validation,
not a complete audit. Discard all results if iteration raises, including a
partition-change error. Consumers must exhaust or explicitly close iterators.

## Remaining admission and integration work

1. Integrate #89/#90 and #74 after current-base CI and review; reconcile #66
   single-pass labels and #67 conflicts without duplicate training pipelines.
2. Connect the existing 1m, historical SEC, fundamental, treasury terms,
   identity, corporate action and broker read-only adapters. Real-time and
   macro feeds require separately verified providers and rights.
3. Reuse certification/evidence modules for immutable source revisions,
   actual availability timestamps, stable historical IDs, delisting coverage,
   corporate actions and dataset rights. Reject unknown availability.
4. Reuse existing resumable download and factor build jobs, and their quality
   quarantine; add unified orchestration and coverage reporting only after
   dependency review. Do not delete raw inputs or bypass quarantine.
5. Integrate Research Engine through an independently verified admission gate;
   do not relax the synthetic trainer or grant execution permissions.
6. Run full Windows/Linux CI on the final combined head and benchmark real
   local partitions. No production admission or performance gain claimed.

## Local verification (PowerShell, repository root)

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -e ".[lake,calendar,research-io]"
if ($LASTEXITCODE -ne 0) { throw "Install failed" }
& $py -m unittest discover -s tests -p "test_unified_data.py" -v
if ($LASTEXITCODE -ne 0) { throw "Unified tests failed" }
& $py scripts/maintenance/audit_source.py
if ($LASTEXITCODE -ne 0) { throw "Audit failed" }
& $py -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw "Regression failed" }
& $py scripts/maintenance/benchmark_unified_data.py --dataset equity_daily --start 2020-01-01 --end 2020-01-31 --column adjusted_close
```

Benchmark only ranges actually present in local_data. Report wall time, CPU
time, row throughput and Python allocation peak; native DuckDB memory and
process RSS are not measured by tracemalloc. Repeat with the same source and
projection before comparing results.

Raw minute scans reuse the existing raw QC, reject any invalid source rows, misaligned timestamps, and RTH rows outside the declared month. Duplicate selected normalized symbol/minute keys fail during iteration. No raw repair or new data file. RTH clock filtering is not exchange-calendar certification. The full raw QC requires additional scans; benchmark before repeated research use.

### SEC and security evidence

`iter_batches("sec_filings", start="2020-01-01", end="2020-01-02", symbols=("123",), as_of="2020-01-03", purpose="exploratory")` reads the existing V5.2 candidate filing index without creating or rebuilding it. Explicit CIKs, complete requested index-quarter inventory and strictly prior filing dates are required. Index digest and parser version are included by default, but are recorded provenance, not proof of current source authenticity, complete filings or acceptance timestamps. Same-day filings are blocked. The separate `review_security(as_of=..., trade_date=...)` facade reuses the existing read-only evidence snapshot for historical memberships, listing intervals, corporate actions, conflicts and unresolved records. Its existing algorithm materializes the visible ledger; it is not a bounded market-bar stream. It is still a research-only projection, not production PIT certification.

### Fundamental snapshots and file fingerprints

`iter_fundamentals(path, start=..., end=..., as_of=..., purpose="exploratory")` streams both existing annual fundamental CSV formats with their provenance fields, validates their selected ratios using the existing screener validator, rejects same-day close-derived values, and requires unique chronological records. It never assigns a permanent security ID or forward-fills a missing release. CSV rows are scanned rather than indexed. `parquet_manifest(dataset, start=..., end=...)` reuses the existing SHA-256 fingerprint function to hash monthly partitions in chunks. It records content changes without copying data, is not a signed admission receipt, and does not validate the Parquet schema or source authenticity. SQLite and DuckDB databases may have WAL state and are deliberately excluded from this file manifest. The existing data builder and audit checkpoint workflows remain unchanged.

### Maintenance and private broker review

`archive_plan(start_month, end_month)` is a no-write inventory of raw, converted, quarantine and factor files, including months outside the advertised archive. File presence is not coverage or integrity. `audit_archive(..., resume=True, dry_run=True)` delegates to the existing resumable monthly QC workflow. Only an explicit `dry_run=False` writes existing QC metadata; it never downloads or alters raw data. Existing download and factor build commands remain the canonical paths. No automatic download or conversion is enabled. `review_broker_capture(capture_path, mapping_path)` consumes private local JSON files and reuses the existing IBKR mapping diagnostic, including freshness, currency and mapping blockers. Synthetic captures require explicit opt-in and remain labeled; no network, credentials, order submission or margin approval. Returned financial data must remain local. Do not share the full report or commit it.
