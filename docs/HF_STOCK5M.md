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


### V5.1 SEC index anomaly handling

A quarterly SEC index can contain isolated records that fail date, field,
or path validation. The collector now retains the original response bytes
and omits those records from normalized candidates. Rejection counts,
categories and up to 25 line-numbered examples appear in manifest stats.
The conservative provisional threshold is at most 100 rejected rows and
at most 0.1% of observed rows, with at least one valid row. A run containing
such a quarter reports CANDIDATES_COLLECTED_WITH_REVIEW, not clean coverage.
Larger anomalies or missing headers fail that quarter, store a rejection
manifest under collector/rejected, and continue independent quarters.
HTTP/access and network failures still stop that provider for the run.
This is a collection policy, not proof that rejected records are corrupt.
The original raw bytes remain the evidence of every omitted row.


## V5.2 historical SEC filing intelligence

Build a local DuckDB index of candidate filings by reparsing the original
V5.1 quarterly master-index objects and verifying their SHA-256. No network
or canonical price scan. One quarter per transaction; unchanged source
hash/parser version and matching row count allow reuse. Identical index
rows are deduplicated; different issuers sharing a key cause a visible
partition failure. The index covers selected candidate forms, not every
filing or all corporate actions.

~~~powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m atlaspm.cli market-db hf-5m-filings-build-v52
& $py -m atlaspm.cli market-db hf-5m-filings-select-v52 --ciks 320193 --start 2020-07-01 --end 2020-09-30 --kind split --limit 20
~~~

CIK 320193 is an example issuer filter, not a historical security ID.
Supported event categories: split, exit, listing, merger, ticker, dividend.
Selection filters by **filing date**, not event date. It is deterministic,
bounded, and reports missing requested quarters and truncation. A failed
refresh can leave the previous indexed partition intact; inspect the build
report before relying on any cached selection.

Optional explicitly bounded SEC body retrieval (requires contact agent):

~~~powershell
& $py -m atlaspm.cli market-db hf-5m-filings-collect-v52 --ciks 320193 --start 2020-07-01 --end 2020-09-30 --kind split --limit 20 --max-requests 20 --fetch
~~~

Without --fetch only cached bodies are parsed. With --fetch, requests
are serial with a 0.5 second minimum pause; access/network failure stops
the download loop. Raw bodies are content-addressed, hashed and retained.
Extracted hints contain text excerpts, keyword/ratio matches, filing date
lower bound for knowledge, and ambiguity flags. Effective dates and
security IDs are deliberately null. Primary-form-only extraction misses
exhibit-only disclosures; it is not a complete parser. Text regex hits
can be historical, proposed, negated, canceled, unrelated or ambiguous.
The header/primary form check is not independent accession/issuer
authentication. Do not use the hint outputs as V5 certified CSVs.
No automatic historical ticker-to-CIK mapping or company action approval.

Reports are under local_data/stock_5m/huggingface/quality/certification_v5/filings_v52/
(index_report.json, evidence_report.json, index.duckdb, hints/).
No external evidence is redistributed. Data rights and training/live
approval remain unverified/false. No broker interfaces.


## V5.3 historical security master (research only)

An append-only local SQLite claim ledger records effective intervals and
separate documented knowledge dates. It does not infer securities from
CIKs or translate current ticker lists into historical membership.
The latest revision per declared source family strictly before the
decision date is considered; same-day publication is unavailable.
Conflicting latest revisions, ties, missing or changed evidence bytes,
overlapping identities and duplicate economic event keys are unresolved.
A retraction needs the same corroboration as an assertion.

~~~powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m atlaspm.cli market-db hf-5m-security-inventory-v53
& $py -m atlaspm.cli market-db hf-5m-security-snapshot-v53 --as-of 2020-01-02
~~~

An empty inventory is expected until evidence has been reviewed.
V5.2 text hints are counted but not automatically imported. To ingest
real reviewed evidence, prepare a local UTF-8 JSONL file with one claim
per line and exactly the following fields:

- record_id: stable logical interval/event ID, consistent across sources
  and revisions; record_type: IDENTITY or ACTION.
- source_family: declared original evidence producer (not a mirror);
  source_url: HTTPS provenance; evidence_path: original local evidence
  under local_data; evidence_sha256: SHA-256 of those bytes.
- known_date: independently documented knowledge date, not effective date
  or today's retrieval date; reviewer: explicit reviewer ID.
- state: ASSERTED or RETRACTED; payload: null for retraction.
- IDENTITY payload: security_id (stable share-class ID, not CIK), symbol,
  exchange (supported MIC), valid_from, valid_to (exclusive or null).
- ACTION payload: security_id, kind, effective_date, value, currency,
  successor_id, new_symbol. SPLIT uses a positive non-unit decimal string
  (new/old shares) and null currency; CASH_DIVIDEND and CASH_EXIT use
  positive USD per share. MERGER uses successor_id; TICKER_CHANGE uses
  new_symbol; DELISTING records date only. Unused fields must be null.
  Complex merger economics, non-USD payouts and total-return computation
  are not supported; record separate evidence rather than guessing.

~~~powershell
& $py -m atlaspm.cli market-db hf-5m-security-ingest-v53 --claims local_data/reviewed_claims.jsonl
& $py -m atlaspm.cli market-db hf-5m-security-snapshot-v53 --as-of 2020-07-01 --trade-date 2020-06-30
# Only with independently verified raw price semantics:
& $py -m atlaspm.cli market-db hf-5m-security-adjust-v53 --security-id YOUR_VERIFIED_ID --bar-date 2020-06-01 --as-of 2020-07-01 --raw-unadjusted
~~~

At least two declared families and two different evidence hashes must
agree on a payload. This is a provisional policy, not independent source
authentication. Different bytes do not prove independent origins, and
reviewer IDs are declarations, not cryptographic attestations. Snapshot
output includes known future effective events; members are only those
effective on the requested trade date. A provisional split multiplier
uses only visible resolved splits and refuses unresolved records or
unsupported exits; it cannot prove missing actions do not exist.
No destructive rewriting of canonical bars, no export of a revised
snapshot as a globally PIT-correct V5 CSV, no automatic training approval.
V5 remains the external certification gate; its unresolved authenticity,
coverage, adjustment and rights requirements remain in force.

Local SQLite ledger and inventory: local_data/stock_5m/huggingface/quality/certification_v5/security_master_v53/.
Do not commit source data or reviewed claims to GitHub.


## V5.4 targeted market anomaly investigation

Start with one real archive month, with the preceding month as lookback.
The scanner reuses V4 full-NYSE-slot projection and the factor mart's
bounded-memory DuckDB connection, storing a separate research daily
cache. It does not alter canonical OHLCV or factor partitions.
Daily caches bind source size/mtime, calendar digest, parser version
and cached Parquet SHA-256. Do not run concurrent writers.

~~~powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m atlaspm.cli market-db hf-5m-discover-v54 --month 2026-03 --threshold 1.5 --limit 200 --memory 2GB --threads 2
& $py -m atlaspm.cli market-db hf-5m-investigate-v54 --month 2026-03 --max-events 10 --per-event 5
# After reviewing the offline plan and configuring ATLASPM_SEC_USER_AGENT:
& $py -m atlaspm.cli market-db hf-5m-investigate-v54 --month 2026-03 --max-events 10 --per-event 5 --max-requests 20 --fetch
~~~

Consecutive NYSE-session gaps compare the prior final full slot's close
to the current first full slot's open. Default symmetric multiplicative
threshold 1.5 means ratio >=1.5 or <=1/1.5; nearby integer or reciprocal
ratios are only split-like hypotheses. Observation gaps of at least ten
calendar sessions are separately flagged; missing data, halts and
ticker reuse can cause gaps. Interior session completeness is unverified.
First/last observations cannot establish IPO or delisting; the
one-month lookback is censored and no disappearance date is inferred.
The queue reports total detected and truncation; the strongest anomalies
are not a representative sample or a training feature.

Investigation searches the V5.2 local SEC filing index using candidate
CIKs from the latest captured V5.1 current ticker snapshot, with an
event window from 14 calendar days before to 30 after the observed date.
This present-day mapping is explicitly a search lead, not PIT identity.
Ambiguous and absent CIK leads stay unresolved. Eligible candidates
are prioritized for a bounded investigation, and queue-wide lead counts
are reported. The lookahead window is for retrospective evidence
research only; not for signals at the event date.
No current mapping is written to a historical ledger. The V5.2
collector keeps original filing bodies and unverified text hints.
A global request budget and network-failure stop apply across events.
No automatic reviewed claim ingestion, event confirmation, split
adjustment, certification, or training authorization. Independent
evidence, historical ticker/share-class identity and completeness
must still be supplied.

Local output: local_data/stock_5m/huggingface/quality/certification_v5/discovery_v54/
(daily/, queues/YYYY-MM.json, latest.json). V5.2 index must be built
before meaningful filing selection; missing quarters are reported.


## V5.5 retrospective evidence matching

This command combines V5.4 monthly discovery and V5.2 targeted filing
collection, then reopens hash-verified original SEC bodies. It compares
issuer/accession headers, explicit effective-date and ticker wording,
split ratio (new shares / old shares), and the observed overnight
price ratio. A 15% provisional residual-price tolerance is diagnostic,
not independent confirmation. Uncertain wording, incompatible ratios,
and incomplete or mismatched headers remain review items.

~~~powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m atlaspm.cli market-db hf-5m-match-v55 --start-month 2026-03 --end-month 2026-03
# With ATLASPM_SEC_USER_AGENT configured, explicitly allow bounded downloads:
& $py -m atlaspm.cli market-db hf-5m-match-v55 --start-month 2026-03 --end-month 2026-03 --fetch --max-requests 20
~~~

The V5.2 index must already exist with relevant quarters imported.
A date range processes one month at a time and persists each report.
Reruns reuse validated daily and SEC body caches, but recompute review
results rather than blindly trusting previous checkpoints. A global
network budget applies; failed network attempts or uncertain failures
stop subsequent network requests for the run. The strongest bounded
candidate set is investigated, not the entire historical event universe.
Reported coverage counts are discovery counts, not certified market
coverage. Offline runs make no requests.

Optional --reference-jsonl accepts externally reviewed split assertions,
one JSON object per line under local_data, with fields symbol,
effective_date, new_over_old_shares (positive integer fraction string
such as 1/25), security_id, source_family, source_url, evidence_path,
evidence_sha256, known_date. The HTTPS source must be non-SEC, with
original locally retained bytes outside the certification pipeline.
The engine verifies hashes, compares ratios and flags conflicts, but
does not authenticate publisher independence or prove the supplied
historical share-class mapping. No optional reference is synthesized.

The output directory is
local_data/stock_5m/huggingface/quality/certification_v5/matching_v55/
with months/YYYY-MM.json and latest.json. Evidence paths and excerpts
are for local review. No claim is automatically approved or ingested.
After actual source review, use V5.3's existing reviewed-claims JSONL
ingest, with separate known dates and documented security identity.
Do not equate a filing date or retrospective agreement with earlier
knowledge. Primary-form regex matching misses many legitimate events;
dividend, merger, exit and ticker-change terms need separate validation.
No full-history completeness, source independence, price adjustment
semantics, rights, training or trading authorization is inferred.


## V5.6 historical SEC cover observations

Use the completed V5.2 index without rebuilding or downloading index files.
This reader samples one latest original 8-K or 6-K per historical CIK per
quarter in a requested year. It does not consult a current ticker directory.
Start with a bounded explicit issuer sample:

~~~powershell
& $py -m atlaspm.data.hf_stock5m_identity_v56 --year 2020 --ciks 320193 789019 1045810 --max-requests 12 --fetch
~~~

Omit --fetch to inspect only cached bodies without network requests. Use
the same existing SEC contact User-Agent and shared V5.2 body cache.
Request budgets count network attempts only; a failed network request
stops further downloads. For a broad sample omit --ciks, use
--max-issuers (1..100), and continue with --after-cik only once
page_advance_allowed is true and more_issuers is true. Repeat the same page
to finish missing cached bodies. A cache error requires review rather
than silent replacement. The cursor is lexical over padded historical
CIKs, not a guarantee of historical listed-security coverage.

The parser verifies accession, issuer CIK and filed date headers, selects
one primary form, and accepts explicit DEI security title, trading symbol
and exchange facts only in the same table row and XBRL context. Unknown
exchange names, cross-context joins and conflicting ticker observations
are rejected or marked for review. A filing-date observation is not an
effective interval or a stable share-class identity. Older filings
without inline DEI cover facts remain unresolved. The current module does not resolve historical ticker reuse, certify
corporate actions or import V5.3 claims.

Reports: local_data/stock_5m/huggingface/quality/certification_v5/identity_v56/latest.json
and bounded page reports. Body references retain raw SHA-256. No
historical identity, data rights or training permission is granted.


### V5.7–V5.8 legacy cover and lifecycle leads

For documents without inline XBRL tags, the extractor accepts an untagged
HTML table only when the first row has the exact title-of-class, trading
symbol and registered-exchange column headers. Each subsequent row must
have exactly three cells; nested tables, merged cells and inline tagged
tables are not accepted by this fallback. A two-column pre-ticker-disclosure
table or a narrative ticker mention does not establish a mapping. Older
filings frequently lack explicit ticker columns, so this is a limited
legacy HTML fallback, not a complete 2000–2018 parser. Tagged documents
never fall back to untagged text to evade context validation.

Historical issuer pages now include CIKs with only registration, exit or
merger filing leads, even when no 8-K/6-K cover sample is available.
The additional index query is offline and bounded to 10,000 leads per
page; use a smaller issuer page if truncated. The report distinguishes
exit, registration and merger registration forms and exposes
issuers_without_sampled_cover_filing. All effective dates and stable
security IDs remain null. Filing dates are not actual listing/delisting
dates. A registration filing may never become effective, and a Form 15
does not necessarily establish exchange delisting. No automatic ticker
change or action certification is attempted.

page_advance_allowed is false for missing index quarters, truncated
event leads, missing bodies or collection/cache errors. Use this flag
rather than page_complete alone before advancing a cursor. Reuse the
same historical SEC index and raw body cache; no index rebuild needed.


### V5.9 reviewed evidence integrity and visible-action return reconciliation

The reviewed V5.3 ledger now validates canonical claim JSON, claim SHA-256
and indexed metadata before applying the as-of filter, including before
append. This detects accidental edits, not a malicious rewrite of every
digest. Inside the pipeline evidence tree only original content-addressed
collector objects or filing bodies can be cited; generated hints, reports,
queues and manifests cannot be primary evidence. Visible evidence bytes
are rehashed. Two declared source families and distinct bytes are still
only declared corroboration, not independently authenticated sources.

With reviewed V5.3 claims and explicit local endpoint marks, use:

~~~powershell
& $py -m atlaspm.data.hf_stock5m_visible_returns_v59 --file local_data/marks.jsonl --as-of 2020-04-01 --raw-unadjusted
~~~

Each JSONL object contains exactly security_id, start_date, end_date,
start_symbol, end_symbol, start_close, end_close. Prices are positive
decimal strings. For example (illustrative only):

~~~json
{"security_id":"ID1","start_date":"2020-02-01","end_date":"2020-03-31","start_symbol":"ABC","end_symbol":"ABC","start_close":"100","end_close":"55"}
~~~

The snapshot is retrospective at the given as-of date, strictly after
the return endpoint; it is not a per-decision training feature snapshot.
Intervals must continuously cover the requested dates, and ticker changes
must reconcile with explicit reviewed actions. Exchange transitions,
identity gaps, unresolved visible claims, mergers, delistings, cash exits
and same-day split/dividend share-basis ambiguity block calculation.
Visible splits change shares and cash dividends accrue per then-current
share count; no reinvestment, tax, payment timing or execution costs are
modeled. Outputs are split-adjusted price return and visible economic
entitlement return. Raw marks are caller-declared and hashed, not
independently verified; no calendar/session coverage or adjustment
completeness is proven. Limit: 1,000 rows and 4 MiB input, one snapshot per
run. Output is returns_v59/latest.json under certification_v5.
No source market parquet is modified, no formal label is published and no
real training or execution permission is granted. The missing independent
historical sources, full corporate-action coverage, vendor price semantics
and rights remain external blockers.

### Legacy periodic-filing sampling (optional)

A local 2000 pilot fetched 100 current-report bodies but produced no
supported cover observations. This is a selection/extraction coverage
limitation, not proof that historical identities are absent. V5.2 now
indexes 10-K, 10-K405, 10-KSB, 20-F, 40-F, 10-Q and 10-QSB from the existing
hash-verified raw SEC indexes (parser v52.2). Rebuild the four local year
partitions; no quarterly index redownload is needed when raw objects exist.

~~~powershell
& $py -m atlaspm.cli market-db hf-5m-filings-build-v52 --start-quarter 2000-Q1 --end-quarter 2000-Q4
& $py -m atlaspm.data.hf_stock5m_identity_v56 --year 2000 --periodic-first --max-issuers 20 --max-requests 20 --fetch
~~~

The optional strategy selects at most one body per historical issuer: earliest
annual filing in the year, otherwise earliest quarterly filing. It refuses
missing or stale index quarters and preserves the old current-report strategy
as the default. Periodic-only sampling is not a complete historical universe;
issuers without periodic filings are excluded from that pilot. Modern cover
facts and exact legacy three-column tables remain the only supported
observations; older two-column covers do not prove a trading symbol.
No stable identity, historical interval, action or training approval is inferred.

For untagged periodic primary forms, an additional narrow text matcher
reports separately bounded excerpts explicitly describing the registrant's
common stock, exchange and quoted symbol. It does not elevate those leads
to supported cover observations, even if a match is unique. It cannot
resolve temporal scope, share-class identity or ownership of a ticker;
no lead is ingested automatically. Exhibit-only text, tagged documents,
unquoted ticker guesses and generic issuer name matches are not supported.

### Expanded legacy Item 5 diagnostic leads

The 2000 local pilot exposed several explicit listing syntaxes missed by the
initial matcher. The additional matcher is bounded to Item 5 market sections
and retains reported exchange text (including combined venues), named share
classes and explicit ticker punctuation. It never assigns a primary MIC,
security ID or historical interval. Dated ticker/venue transition statements
are emitted in a separate review-only list, preserving the filing-known date
and leaving effective_date unset. Duplicate primary documents remain blocked.
The existing cover and text-lead contracts, review and permission gates remain.
Re-run the same 20-issuer periodic pilot without --fetch to reuse cached files.

### Historical identity candidate market review (offline)

Re-extract the same cached annual-filing page and count exact candidate
symbol overlap against the local full-NYSE-slot view. For the 2000 pilot:

~~~powershell
& $py -m atlaspm.data.hf_stock5m_identity_market_review --year 2000 --start-month 2000-03 --end-month 2000-10 --max-issuers 20
~~~

The report distinguishes before/on/after filing dates and reported change
dates, marks same-symbol claims across sampled issuers, and identifies missing
partitions without treating them as negative evidence. It processes at most
12 months and 500 unique candidate symbols, using one connection at a time
with a 512MB DuckDB memory setting (not total process RAM). No network,
original-bar replacement, independent identity verification, certification,
claim ingestion, backtest or training permission. The review is retrospective
triage only. Source page and body provenance remain linked; report is saved
under identity_v56/market_review/latest.json. Repeat with a smaller issuer
page if the symbol budget is exceeded.


### Declared external historical interval review (optional)

The market review can additionally generate an external evidence request
queue for each SEC listing or change endpoint. No external source is
downloaded or invented. Example, using the original cached 2000 pilot:

~~~powershell
& $py -m atlaspm.data.hf_stock5m_identity_market_review --year 2000 --start-month 2000-03 --end-month 2000-10 --max-issuers 20 --reference-as-of 2026-11-25
~~~

To compare against separately reviewed external reference data, add
--reference-file local_data/historical_reference.jsonl. Every JSONL row
must contain exactly the following fields (synthetic example, not evidence):

~~~json
{"reference_id":"ref1","source_family":"vendor","source_url":"https://example.org/archive","evidence_path":"local_data/external/raw.txt","evidence_sha256":"<actual 64 lowercase hex SHA-256>","reviewer":"reviewer1","known_date":"2000-03-20","cik":"0000000001","symbol":"ABC","source_security_key":"vendor123","exchange":"XNYS","valid_from":"2000-03-01","valid_to":"2000-04-01"}
~~~

An operator must derive the explicit CIK, vendor security key, MIC, known
date and interval from reviewed external original records; today's ticker
list is not a substitute. valid_to is exclusive (null is an asserted open
interval, not a verified end). Reference files and originals stay in
local_data; SEC URLs, pipeline-derived reports, duplicate IDs and hash
mismatches are rejected. Limits: 16 MiB normalized JSONL, 20,000 rows and
100 intervals per symbol. Reference agreement is a diagnostic only:
CIK+symbol+MIC is not a verified share class; source family, source URL
and reviewer are declarations, not proof of independence or authenticity.
An explicit combined exchange name remains unresolved.

Point checks use the filing date or the reported change date (old endpoint:
previous calendar day), not inferred effective dates. The review cutoff
is exclusive. Separate observed listing-candidate symbol-day statistics
require both SEC and reference known dates strictly earlier than the
session, and exclude change-only endpoints. They are deduplicated across
filings and do not measure full-universe coverage. Same-symbol CIK and
source-key conflicts block agreement; lack of agreement is not proof of
nonmembership. This module never writes the V5.3 reviewed ledger, grants
backtest/training authorization, or modifies raw bars. Independently
verified evidence and manual review remain required.

A source-local key with contradictory simultaneously effective and visible
symbol/CIK/venue assignments also blocks agreement. Later-known conflicts
are not used in earlier daily statistics. A source key is never promoted
to an AtlasPM global security ID. At most 100 normalized rows may share a
source family/key in a batch.

### Bounded reviewed-claim preview and atomic import

The existing V5.3 import accepts --preview and --expected-sha256. Preview
uses the same validation and duplicate counting as import but never creates
or writes the ledger. After independent review of original evidence and
normalized assertions, a caller may bind the exact input bytes by providing
the preview input_sha256 when explicitly importing. Counts are recomputed in
the transaction; preview does not reserve a ledger revision or establish
source authenticity, interval correctness or approval. Limits: 16 MiB input,
10,000 claim rows, 1,000 distinct source objects and 2 GiB aggregate evidence
file size. Both input bytes and original evidence hashes are rechecked before
commit; changed evidence or input rolls back the claim transaction. This is
not a filesystem snapshot or defense against an adversarial concurrent
writer; keep evidence stable throughout import. Duplicate JSON members are
rejected, as are self-referencing claim inputs. No permission is granted.

### Shared identity continuity for provisional split adjustments

The V5.3 split multiplier and V5.9 visible-action return reconciliation now
reuse the same resolved identity/action path check. The multiplier requires
identity coverage through its inclusive cutoff, no gaps, an unchanged
exchange, and explicit matching ticker-change assertions at every symbol
transition; exits remain unsupported. This is stricter than the previous
start-only identity check. The split multiplier includes the contributing
identity and visible action record IDs. An explicit Boolean unadjusted-input
declaration is still required, not proof of vendor price semantics. No raw
prices are overwritten and no identity, completeness, or training approval
is inferred from these provisional results.

### Daily reference sample coverage guards

Reconciliation requires at least one comparable day for every explicitly
requested symbol. A matching symbol cannot mask another requested symbol
that is missing from both sources. Local duplicate/off-grid buckets and
nonfinite or invalid close/volume values block the affected day. Both raw
minute count and distinct minute count must be five for volume comparison
and open/close boundaries. Zero reference volume agrees only with zero
local volume; positive local volume is flagged without dividing by zero.
The report distinguishes price-compared and volume-compared day counts;
no comparable volume has null maximum percentage difference. This remains
sample reconciliation, not source authentication, full historical coverage,
corporate-action completeness or training approval. No source data changes.
