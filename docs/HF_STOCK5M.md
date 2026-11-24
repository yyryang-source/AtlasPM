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
- Source Parquet requires timezone-aware timestamps and structurally valid OHLCV.
  Missing or invalid ticker/timestamp, missing columns, or large-scale bad source
  data still reject the whole month (fail closed). When invalid OHLCV rows are
  **at most 100 rows AND at most 0.01%** of the original source and their
  ticker/timestamp are identifiable, the entire affected ticker/5m bucket is
  quarantined instead of fabricating a five-minute bar from the remaining
  minute records. Source records are never edited; all excluded five-minute
  bucket identifiers and counts are recorded under `quality/`.
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

## Recovering from "Source QC failed: ... invalid OHLCV rows"

A damaged network download is not the only reason for a QC failure. For
example, a source minute can have its low price above its opening price,
violating `low <= min(open,close)`. The system must **not** silently adjust
prices because the true trading range is unknown. Affected whole five-minute
buckets are quarantined and omitted from usable prices.
The source is retained. No automatic certification for model training follows.

After updating your local clone, retry the already-downloaded source without
`--refresh` (only if you have verified legal data-use rights):

~~~powershell
& .\.venv\Scripts\python.exe -m atlaspm.cli market-db hf-5m-backfill --start-month 2000-01 --end-month 2000-01 --max-months 1 --session rth --acknowledge-rights
& .\.venv\Scripts\python.exe -m atlaspm.cli market-db hf-5m-audit --month 2000-01 --session rth
~~~

Review the private conversion QC JSON: `invalid_source_rows`,
`excluded_invalid_source_5m_buckets`, `excluded_total_5m_buckets`,
`quality_status`, and `quarantine`. The offline audit also exposes
`invalid_raw_source_rows` and `invalid_source_5m_buckets_quarantined`.
All months containing source anomalies remain `REVIEW_REQUIRED`.
If a month is still rejected, inspect the actual message before retrying or
downloading anything; thresholds must not be raised to make it pass.

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
- ambiguous_buckets_quarantined counts duplicate-minute buckets.
  invalid_source_5m_buckets_quarantined counts five-minute intervals excluded
  because their underlying minute OHLCV was invalid. total_5m_buckets_quarantined
  counts their union, without double-counting. These rows never become tradeable
  prices and keep quality_status at REVIEW_REQUIRED.

quality_status will stay REVIEW_REQUIRED even if no structural error
is found: there is no independent certification of corporate actions,
ticker identity, listing/delisting membership, historical bias,
data usage rights, or point-in-time availability. No model is approved
for training or live trading by the audit.

This is a local research data inspection, not a performance backtest.

## Full-history offline quality audit V2 (resumable, 315 months)

After locally confirming the 315 month-level 5m Parquet and conversion QC
files for 2000-01 through 2026-03 exist, run a **read-only** quality scan.
This work reads only the local converted Parquet and local conversion QC;
it neither downloads the original dataset nor changes any price record,
trading permissions, or training eligibility.

~~~powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -e ".[lake,calendar,hf]"

# Verify planned coverage without any Parquet scans or network:
& $py -m atlaspm.cli market-db hf-5m-audit-batch --dry-run

# Initial small scan, to inspect first two months:
& $py -m atlaspm.cli market-db hf-5m-audit-batch --start-month 2000-01 --end-month 2000-02

# All 315 published months (or resume a prior interrupted run):
& $py -m atlaspm.cli market-db hf-5m-audit-batch

# Force a full re-audit after upgrading QC logic (no downloads):
& $py -m atlaspm.cli market-db hf-5m-audit-batch --no-resume
~~~

Monthly audit results remain at
`local_data/stock_5m/huggingface/quality/audits/session=rth/YYYY-MM.json`.
The aggregate summary and per-month CSV are stored under
`local_data/stock_5m/huggingface/quality/batch/session=rth/` as
`latest_summary.json` and `latest_months.csv`. Per-month metadata
checkpoints live under the same directory in `months/`, so interruption
does not discard finished checks. Each resumed month checks source Parquet
size/mtime, QC metadata size/mtime, and a SHA-256 hash of the small
monthly audit JSON. This is **cache invalidation, not cryptographic
verification of the source Parquet**: when provenance changes without file
metadata changes, use `--no-resume` to force a fresh scan.

Statuses are `REVIEW_REQUIRED` (structurally audited, still unverified),
`STRUCTURAL_QC_FAILED` (actual structural issue found), `MISSING_FILE`
(no local converted Parquet), and `AUDIT_ERROR` (audit did not finish).
Failures and missing partitions are not cached as successes and do not block
auditing subsequent months. Whole missing NYSE sessions appear as review
reasons; they are distinct from evidence of corrupted OHLCV rows. An absent
current-day issuer/ticker does not automatically indicate a source outage:
historical IPO dates, ticker reuse and delisted identities need a separate
point-in-time reference.

The aggregate report includes selected/audited month counts, per-month
statuses, missing market sessions, source-anomaly counts, total quarantined
5-minute buckets, and pointers to inspectable local JSON and CSV.
`all_months_structurally_audited=true` means only **structural** auditing
finished without failures. The output always has
`coverage_verified=false`, `data_rights_verified=false`,
`historical_point_in_time_membership_verified=false`,
`approved_for_model_training=false`, `research_only=true`, and
`live_execution_enabled=false`. Never translate `REVIEW_REQUIRED` into
a model-training or trading authorization.

## Historical NYSE sub-five-minute session boundaries

Some historic NYSE sessions start or end between the fixed 5-minute
boundaries used by the DuckDB aggregator. Six examples found during the
315-month audit (NYSE/ET): 2001-09-17 09:33 open, 2001-10-08 09:31
open, 2003-03-20 09:32 open, 2004-06-07 09:32 open, 2005-06-01
15:56 close, and 2006-12-27 09:32 open. Rejecting any session whose
duration was not divisible by 5 minutes caused `AUDIT_ERROR`, even though
these are valid market-calendar boundaries.

The audit counts fixed UTC 5m slots **overlapping** the actual
NYSE [open, close) session, while still flagging bars entirely outside
the NYSE schedule for investigation. For the first/last partial bucket
it compares the number of source 1m records with the number of NYSE
calendar-open minutes. The full set of irregular NYSE dates is listed in
`non_5m_aligned_nyse_sessions`.

**Important all-market caveat:** This dataset includes US stocks from
multiple exchanges. The NYSE special open/close time does not establish
the corresponding Nasdaq or individual security's listing-venue session.
For example, a 09:30-09:35 5m bucket can have more 1m records than the
NYSE-open portion of a delayed-opening 09:33 session. That difference is
meaningful evidence but **not independently proof of a corrupted price
bar** for a ticker with unknown historical exchange membership.
`nyse_boundary_minute_count_exceeded` preserves the exact count, and
`NYSE_BOUNDARY_MINUTES_EXCEED_EXCHANGE_OPEN_VENUE_UNVERIFIED` is added
to `review_reasons`. The status remains `REVIEW_REQUIRED` when no
other structurally provable error exists. Actual invalid OHLCV,
duplicates, missing QC metadata and separate true structural failures
remain `STRUCTURAL_QC_FAILED`. Do not repair or delete prices based
solely on this NYSE-versus-all-venues discrepancy.

The monthly audit schema is now `atlaspm_hf_5m_research_qc_v3`.
The batch resume logic selectively invalidates older V2 monthly audit
checkpoints **only if** the report contains this NYSE boundary overflow.
Unaffected V2 scans can be reused after file signatures and audit-JSON
hash checks pass. Older V1 and unknown schemas always require rescanning.
This avoids unnecessarily re-reading the entire 25+ GiB lake solely
for a classification change. No market data is redownloaded.

To inspect only the
six previously errored months first:

~~~powershell
$py = ".\.venv\Scripts\python.exe"
foreach ($m in @("2001-09","2001-10","2003-03","2004-06","2005-06","2006-12")) {
    & $py -m atlaspm.cli market-db hf-5m-audit --month $m --session rth |
        Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Warning "Audit failed: $m"; continue }
    $a = Get-Content "local_data\stock_5m\huggingface\quality\audits\session=rth\$m.json" -Raw | ConvertFrom-Json
    [PSCustomObject]@{
        Month = $m
        Status = $a.quality_status
        BoundaryBars = $a.partial_nyse_boundary_5m_bars
        MinuteOverflow = $a.nyse_boundary_minute_count_exceeded
        StructuralIssues = ($a.structural_issues -join ";")
    }
}
~~~

After these six targeted audits, rerun the batch command; the new V3
reports invalidate their existing checkpoint hashes automatically:
`python -m atlaspm.cli market-db hf-5m-audit-batch`. Previously
audited, unaffected V2 months resume without full rescan.
Inspect `months_structural_failed` and all actual structural issues
separately. Training and live execution remain disabled.

Remember that the lake spans many US-listed symbols, while this calendar
audit uses NYSE as its reference. Venue-specific histories and individual
ticker point-in-time identity are NOT certified by this check. Existing
structural-failure months may still require inspecting source, dates,
and vendor data; do not suppress legitimate calendar anomalies simply to
obtain an apparently green batch report.

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

## Bulk all-market historical download (2000-01 through March 2026)

The Windows PowerShell runner processes every available monthly source file;
**it does not filter to a current stock watchlist**. Each source month
contains all of its published ticker symbols. This is not proof of a
complete historical US equity universe, point-in-time listings, or
delisted-company correctness.

The previously requested range was 2000-01 through 2026-11. The Hugging
Face publisher's latest visible monthly file remains **2026-03**, so
the 2026-04..2026-11 months are logged as unavailable, not fabricated.

~~~powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -e ".[lake,calendar,hf]"

# Verify monthly counts and unavailable months; no network:
& .\scripts\data\Invoke-HFAllMarketBackfill.ps1 -DryRun

# Only after verifying your Finnhub-derived market-data usage rights:
& .\scripts\data\Invoke-HFAllMarketBackfill.ps1 -AcknowledgeRights
~~~

The default full request is `-StartMonth 2000-01 -EndMonth 2026-11`.
`-MaxMonths 0` means all available months, **not** a one-month cap.
Use `-MaxMonths 2` for a small initial batch, then rerun without that
limit. When resumed, months with both completed Parquet and QC JSON
are skipped; failed months are retried. Any failure is recorded and
the full run reports which months did not complete.

Source monthly 1m Parquet and converted 5m Parquet remain on the local
Windows disk by default. **Expect substantial disk use**: the complete
1992..2026 publisher archive is labeled 87.7 GB *before* adding
converted files, temporary files and Hugging Face caches. For the
2000..2026 subset, capacity depends on selected monthly sizes; having
well over 100 GB free is prudent. A disk-space guard stops processing
when under 5 GiB remain. If you only want 5m and knowingly do not need
1m source history, opt into `-RemoveRaw` (irreversible without another
download); do NOT use this if you want the original 1m data too.

Logs and a durable summary are private, under:
`local_data/stock_5m/huggingface/bulk/progress.jsonl` and
`local_data/stock_5m/huggingface/bulk/latest_summary.json`.
Do not push local_data/ or the source data to GitHub. No background
execution is performed by ChatGPT: this command runs only on your
Windows computer while PowerShell is executing.


## V4 offline session diagnostics (non-destructive)

The original RTH converter uses a fixed 09:30–16:00 ET envelope. The
canonical file is deliberately unchanged: it is not a certified
exchange-session dataset. V4 uses the exact historical NYSE calendar
already used by V3 and reports the mismatch by date and category.

PowerShell from the repository root:

~~~powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m unittest discover -s tests -p "test_hf_stock5m_session_v4.py" -v
& $py -m atlaspm.cli market-db hf-5m-session-v4 --dry-run
& $py -m atlaspm.cli market-db hf-5m-session-v4
~~~

Default targets are failed, partial-boundary, missing or old-schema audit
months; other V3 review months are listed as skipped, not newly verified.
Use --all-months to explicitly scan every selected partition. No downloads
or raw-file modifications occur. The summary is stored under
local_data/stock_5m/huggingface/quality/session_v4/latest.json.
It contains counts by category/date, the source size/mtime signature,
calendar SHA-256, reconciliation against V3 counts, and unresolved
structural issues. Size/mtime is not a cryptographic source hash.
A count match is not a content identity proof. Prior audit files and
their structural-failure statuses are not rewritten.

Python consumers may explicitly call
hf_stock5m_session_v4.install_research_view(connection, month).
This creates a temporary DuckDB view named v4_research_bars containing
only buckets wholly inside the NYSE reference session, with a
bar_available_at_ms column equal to the bucket end (not a verified
vendor publication timestamp). Partial buckets are excluded, not
reconstructed from unknown minute composition. The view is a candidate
session projection, not an OHLCV/identity-certified dataset. It does not
silently replace existing factor or training inputs. Use one connection
per active month or reinstall the view for each month.

Historical listing venue/PIT identity, independent reference comparison,
corporate actions and licensing still require real evidence. V4 never
sets training or live-execution approval. After-close and non-NYSE-date
categories are calendar disagreements, not proof of bad prices.


## V5 evidence-based certification framework (offline, fail closed)

V5 separates evidence validation from proof of data authenticity. It never
sets training or live approval and never modifies the canonical lake.
A no-input run creates a useful blocker inventory without scanning price data:

~~~powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m atlaspm.cli market-db hf-5m-certify-v5
~~~

Supply independently obtained, licensed, local CSV ledgers, not fabricated
rows or today's listing snapshot. Required headers (exact order):

~~~
security_id,symbol,exchange,valid_from,valid_to,known_date,source,evidence_id
event_id,security_id,kind,effective_date,known_date,value,currency,source,evidence_id
~~~

The first header belongs in the identity CSV; the second in the actions
CSV. Security IDs must be stable across ticker changes and distinguish
ticker reuse. Exchange values are supported MICs (XNYS, XNAS, XASE, ARCX,
BATS, IEXG); unknowns are rejected rather than defaulted. valid_from is
inclusive; valid_to is exclusive (blank means open-ended). known_date is
the date the evidence was available, not a fabricated listing date. Only
evidence available before the decision date is eligible; same-day records
are conservatively unavailable. This date-granularity ledger rejects
overlapping symbol or security-ID intervals. It is not a fully bitemporal
revision-history database and does not certify point-in-time knowledge of
every field (including later revisions of end dates).

Actions supported are SPLIT (new shares / old shares, blank currency)
and CASH_DIVIDEND (positive USD per share). Split calculation requires
independently verified unadjusted input; it does not rewrite OHLCV.
Dividend total-return calculation, spin-offs, mergers, delisting returns,
and full corporate-action completeness are not implemented. An empty
action ledger is never proof of no actions.

~~~powershell
& $py -m atlaspm.cli market-db hf-5m-certify-v5 --start-month 2026-03 --end-month 2026-03 --identity-csv local_data\identities.csv --actions-csv local_data\actions.csv --scan
~~~

With --scan, the module uses the V4 full-NYSE-slot candidate projection,
groups observations by symbol/date in DuckDB and counts mapping gaps,
late-known records and mapped observations. This is observed mapping
coverage, not expected historical universe completeness or venue-specific
session certification. Scan errors remain visible. Without --scan no
Parquet is scanned. Both modes write a private latest.json under
local_data/stock_5m/huggingface/quality/certification_v5/.
Ledger SHA-256 fingerprints identify supplied bytes, not authenticity.
The independent daily reconciliation tool remains separate. Rights,
true historical universe, action coverage, adjustment semantics and
source provenance require independently reviewed evidence before any
training approval. No new external data source is bundled.


## V5.1 public evidence collector

Default CLI is a zero-network plan. Explicit collection keeps original
response bytes, SHA-256 content-addressed objects, dated current snapshots,
normalized candidate files and local manifests. Old quarterly indices are
reused unless --refresh is requested (SEC may revise older indices).
The API key is read from the environment and never stored in manifest URLs.

~~~powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m atlaspm.cli market-db hf-5m-evidence-v51
# Replace with your actual contact information, not an example address.
$env:ATLASPM_SEC_USER_AGENT = "YOUR NAME YOUR CONTACT EMAIL"
& $py -m atlaspm.cli market-db hf-5m-evidence-v51 --fetch
~~~

Default range 2000-Q1..2026-Q1 contains 105 completed SEC quarters plus
three current directory requests. Use --max-requests N for a bounded
resumable network batch (cached tasks do not consume the budget).
Use --no-current to skip current directories. Provider errors stop that
provider, not the other independent providers. No automatic retry of
rate-limit errors. Do not run parallel collectors.

Optional Alpha Vantage LISTING_STATUS snapshots require your own API key
in ALPHAVANTAGE_API_KEY and the provider's applicable entitlement/terms.
--listing-dates accepts explicit dates after 2010-01-01; two requests per
date (active and delisted). This does not fill 2000..2009, does not infer
continuous membership between snapshots and cannot resolve stable IDs
by ticker alone. Quota and empty/error responses are not passed as data.

Sources:
- https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
- https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs
- https://www.alphavantage.co/documentation/#listing-status

SEC quarterly master indices identify candidate filings (8-K, 25, S-1
and others), not actual corporate-action terms. The collector does not
download filing contents or infer split/dividend ratios. SEC CIK identifies
an issuer, not a share class. Current SEC/Nasdaq mapping is never backdated.
Candidate output is deliberately NOT a V5 certified identity/action CSV.
Manual or separately verified evidence extraction and stable security
identity resolution are required before V5 ingestion. There is no blanket
permission for market-data training or redistribution.

Local report: local_data/stock_5m/huggingface/quality/certification_v5/collector/latest.json.
No canonical price partitions scanned or changed, no training or order
permission granted.
