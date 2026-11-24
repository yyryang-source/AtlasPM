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
| equity_5m | existing HF RTH converted monthly Parquet |
| option_1m / option_5m / option_daily | market_db option_bars, separate timeframe |
| option_quotes / option_greeks | market_db timestamped tables |
| treasury_auctions / bond_quotes | market_db SQLite |
| factors_hf / factors_sqlite | factor_db source-isolated monthly Parquet |

Unconnected catalog entries deliberately raise DataUnavailable: equity_1m,
equity_realtime, company_fundamentals, sec_filings, macro, security_identity,
corporate_actions, ibkr_accounts, ibkr_positions, option_contracts, bond_terms.
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
