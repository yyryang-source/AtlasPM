# Hugging Face 1-minute US stocks → AtlasPM 5-minute lake

Source: https://huggingface.co/datasets/mito0o852/OHLCV-1m
Advertised range: 1992-01 to 2026-03, approximately 87.7 GB of
month-partitioned Parquet. This does not prove full coverage or allow the
system to invent present-day candles after the advertised end.

## Important data rights

The Hugging Face dataset says its underlying data came from Finnhub.
Public downloads do not constitute an open-data license. Finnhub's
https://finnhub.io/terms-of-service restricts redistribution of source
data and derived results, and limits business use. AtlasPM's MIT code
license does not apply to third-party market data. Verify the rights
for your specific use before downloading, training models, or sharing
derived work. The --acknowledge-rights flag is an explicit user
acknowledgement, not a license grant or verification.

## Windows PowerShell (from project root)

~~~powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -e ".[lake,hf]"

# Safe offline plan, without any remote download
& $py -m atlaspm.cli market-db hf-5m-backfill --start-month 2026-03 --end-month 2026-03 --dry-run

# Only after independently verifying data-use rights, try one month
& $py -m atlaspm.cli market-db hf-5m-backfill --start-month 2026-03 --end-month 2026-03 --max-months 1 --acknowledge-rights
& $py -m atlaspm.cli market-db hf-5m-status

# Offline tests including Parquet conversion
& $py -m unittest discover -s tests -p "test_hf_stock5m.py" -v
~~~

By default, --max-months is 1. Set 0 for unlimited months, only after
checking sufficient time, bandwidth and free local disk space. Use
--refresh to repeat an existing source month; --remove-raw deletes
downloaded source files only after successful conversion.

For a local already-downloaded licensed source, provide --input
local_data\my_ohlcv_2026-03.parquet with matching start/end month;
this mode needs no network or acknowledgement flag.

## Output and queries

One aggregated file per source month, e.g.
local_data/stock_5m/huggingface/parquet/session=rth/year=2026/month=03/ohlcv_2026-03.parquet

Output columns: symbol, trade_date_et, timestamp_ms, open, high, low,
close, volume_shares, transactions (NULL because not supplied),
source_minute_bars and unique_source_minutes.

~~~python
import duckdb
con = duckdb.connect()
rows = con.execute("""
 SELECT symbol,trade_date_et,timestamp_ms,open,high,low,close,volume_shares
 FROM read_parquet('local_data/stock_5m/huggingface/parquet/session=rth/year=*/month=*/*.parquet')
 WHERE symbol='AAPL' ORDER BY timestamp_ms LIMIT 20
""").fetchall()
print(rows)
~~~

- Regular-session default is 09:30 inclusive to 16:00 exclusive,
  America/New_York; --session all preserves off-hour source bars.
- Every five-minute bucket gets earliest open, latest close, maximum
  high, minimum low, summed volume. Missing minute bars are never filled.
- Source Parquet requires timezone-aware timestamps and complete OHLCV.
  Malformed or nonfinite source rows reject that whole month (fail closed).
- A 5m ticker/time bucket with more than five source rows or repeated
  minute timestamps is **ambiguous**. It is *excluded completely* from
  usable prices; the pipeline never arbitrarily chooses a duplicate or
  merges overlapping data. Excluded identifiers and minute counts are
  stored separately under local_data/stock_5m/huggingface/quality/.
- An audit JSON alongside each quarantine file reports source rows,
  candidate/retained bar counts, exclusion rate, and first/last valid
  timestamps. If ambiguous buckets exceed 0.1% of the month or 10,000
  buckets (whichever happens first), the entire conversion is rejected,
  previous successful files stay unchanged, and source data remains.
- **A passing conversion is not certified coverage.** Audit status
  REVIEW_REQUIRED remains when any minute buckets are quarantined.
- 2026 current-day data, split adjustments, historic ticker identities,
  corporate-action consistency, and complete point-in-time listing
  coverage are NOT independently verified.
- For --session all, ET trade date may fall outside the UTC source
  month near boundaries; use trade_date_et, not partition name.
- The lake is separate from daily adjusted closes, IBKR data, and
  trading systems; no order execution, trading permission, or risk-gate
  bypass is enabled.
- No market datasets or personal secrets should enter GitHub.

## Recovering from "5m QC failed: duplicated-minute buckets"

If the source monthly Parquet downloaded successfully, it remains under
`local_data/stock_5m/huggingface/raw/data/`. A failed conversion does not
write its price output and does not require re-downloading the raw source.
Update your GitHub clone, then retry the same `hf-5m-backfill` command
without `--refresh`. The local existing raw file is reused. Inspect
`excluded_ambiguous_5m_buckets`, the `quarantine` path, and
`market-db hf-5m-status` afterward. Do not delete the original data until
you have validated the monthly sample and legal rights.
