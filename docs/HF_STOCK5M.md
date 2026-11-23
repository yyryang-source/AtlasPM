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

## Validate each month before research or AI selection

A successful Parquet conversion is not a benchmark coverage certificate.
The audit below only reads local files, uses NYSE's official trading
calendar, and writes a JSON QC report to the ignored local_data folder.
It does not fetch Hugging Face data, upload price records, or authorize
training/trading.

~~~powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -e ".[lake,calendar,hf]"
& $py -m unittest discover -s tests -p "test_hf_stock5m_audit.py" -v
& $py -m atlaspm.cli market-db hf-5m-audit --month 2026-03 --session rth
~~~

Review the private audit JSON file at:
local_data/stock_5m/huggingface/quality/audits/session=rth/2026-03.json

Key fields:

- five_minute_bars, unique_symbols, first/last trade dates.
- nyse_expected_sessions, missing_whole_trading_dates and
  unexpected_trading_dates (the latter is a structural failure in RTH).
- invalid_five_minute_bars, duplicate_symbol_timestamp_rows,
  out_of_nyse_session_bars (structural failures).
- sparse_five_minute_buckets measures buckets with fewer than
  five source minutes, but a low-liquidity security can legitimately
  have no trading activity in many minutes; missing slots are not filled.
- benchmark_coverage has SPY, QQQ, AAPL, MSFT, NVDA, GOOGL, AMZN, META.
  Its denominator is the NYSE expected regular-session 5m slots;
  thinly traded days or missing vendor data can lower the ratio.
- ambiguous_buckets_quarantined counts the earlier duplicate-minute
  buckets rejected from the prices file.

quality_status will stay REVIEW_REQUIRED even if no structural error
is found: there is no independent certification of corporate actions,
ticker identity, listing/delisting membership, historical bias,
data usage rights, or point-in-time availability. No model is approved
for training or live trading by the audit.

This is a local research data inspection, not a performance backtest.

## Independent daily reference comparison

This next check compares selected liquid symbols' local RTH 5m-derived
daily closes and volumes with a separate daily feed. Nothing is downloaded
without the explicitly requested --fetch-yahoo flag. The existing HF
monthly source file is never re-downloaded or rewritten.

Windows PowerShell, project root:

~~~powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -e ".[lake,calendar,market]"
& $py -m unittest discover -s tests -p "test_hf_stock5m_reconcile.py" -v
& $py -m atlaspm.cli market-db hf-5m-reconcile --month 2026-03 --symbols SPY QQQ AAPL MSFT NVDA GOOGL AMZN META --fetch-yahoo
~~~

Alternatively, use a locally prepared independent reference CSV without
any network request:

~~~powershell
& $py -m atlaspm.cli market-db hf-5m-reconcile --month 2026-03 --symbols SPY QQQ --reference-csv local_data\reference_daily.csv
~~~

CSV headers: date,symbol,close,volume. All rows must be independently
obtained from a separate source for the same sessions. No example
fake market prices should be used for actual comparisons.

Daily close differences above 1% and RTH-only vs reference daily
volume differences above 25% are flagged. Incomplete first/last
5-minute time buckets are omitted from comparisons and explicitly
counted, not silently filled. Daily volume is only compared when the
entire session has all expected five-minute slots with five original
minute records each; missing middle bars suppress volume comparisons. The report is written to
local_data/stock_5m/huggingface/quality/reconciliations/2026-03.json.

WARNING: Yahoo Close may be split-adjusted, while the HF minute
provider's corporate-action adjustment semantics remain unknown.
Volume conventions, coverage, closing auctions and venue aggregation
may differ. Source divergence is diagnostic; the program never
auto-rescales prices or approves AI training. An eight-symbol,
one-month check cannot prove point-in-time market membership or
fully validate historical corporate actions or data rights.
