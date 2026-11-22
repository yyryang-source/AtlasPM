# Phase 1 — US-listed all-market data expansion and readiness audit

This package is inside **AtlasPM stock selection**, separate from
\`atlaspm.quant\` risk/portfolio/IBKR. No broker connection, no portfolio
allocation, no execution, no automated alpha gate approval.

## What is actually being expanded

- Universe: Nasdaq Trader's **CURRENT** Nasdaq, NYSE, NYSE American and
  other supported US exchange listings, filtering explicit ETFs, preferreds,
  warrants, bonds and test issues. Some names are classified
  \`unverified_equity\` and need issuer/security review.
- Quotes: adjusted DAILY close via unofficial Yahoo/yfinance, with
  resumable per-symbol **exact-date-range** cache. The CSV produced by
  \`stock-sync-market\` is a raw input to stock models and screening.
- New: \`stock-market-audit\` checks exact NYSE decision-session quotes,
  quote validity, missing session history, 253-day warmup, listing manifest
  coverage and the actual snapshot date before claiming dataset readiness.
- **Still missing**: point-in-time delisted/IPO membership, trusted
  publication-dated fundamentals, volume/OHLCV data for all names, independent
  model retraining and full-market out-of-sample validation.

A historical backtest on today's current listings is survivor biased.
Do not interpret this work as 2000–2026 unbiased full-US coverage.

## One-command Windows entrypoint (after installing dependencies)

The optional script `scripts/selection/Invoke-USMarketDataExpansion.ps1`
orchestrates listing discovery, price sync, and the independent NYSE
quality audit. It does not train models or connect brokers. You still
need to install `.[market,calendar,ml]` and select the branch first.

```powershell
# First trial: up to 50 missing names
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\selection\Invoke-USMarketDataExpansion.ps1 -AsOf 2026-11-20 -MaxSymbols 50

# Resume all remaining names with the SAME Start and as-of / exclusive end.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\selection\Invoke-USMarketDataExpansion.ps1 -AsOf 2026-11-20 -MaxSymbols 0 -SkipUniverseRefresh
```

The audit runs after each batch. The first 50-name batch should report
`REVIEW_REQUIRED` for incomplete full-US coverage; this is expected.
The script does not register scheduled tasks or automatically start work
in the background.

## Recover from Ctrl+C or Yahoo rate limiting without losing caches

If a large run was interrupted (for example 2,689 ticker CSV+JSON
pairs from 5,830 current US-listed symbols), the previous coverage
JSON is stale because the interrupted process never reached assembly.
**Do not delete or rename local_data/us_equity_cache.** The exact
input interval for existing cache remains 2024-01-01 through the
**exclusive** end date 2026-11-21.

First, reconstruct the current price CSV and completeness report
**offline**—no new Yahoo requests, no new Nasdaq listing download:

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM

powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\selection\Invoke-USMarketDataExpansion.ps1 `
    -AsOf 2026-11-20 -CacheOnly -SkipUniverseRefresh

$r = Get-Content local_data\us_equity_coverage.json -Raw | ConvertFrom-Json
$r | Select-Object listing_candidates,cached_or_downloaded_symbols,missing_symbols,coverage_pct,download_status | Format-List

$a = Get-Content local_data\us_market_data_audit.json -Raw | ConvertFrom-Json
$a | Select-Object market_data_status,eligible_history_and_freshness,eligible_coverage_pct | Format-List
```

This counts **valid matching-date cached symbols**, which may differ
from the raw CSV+JSON pair count if a file was incomplete during
interruption. The audit may remain REVIEW_REQUIRED until enough
current US-listed symbols qualify.

After investigating the provider error messages, resume in **small
bounded batches** rather than \`-MaxSymbols 0\` (which still attempts
every remaining symbol until stopped by safeguards):

```powershell
# Resume at most 50 uncached symbols; submit only 10 per wave,
# 1 worker, 5-second gap between waves, stop at first two 429 errors.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\selection\Invoke-USMarketDataExpansion.ps1 `
    -AsOf 2026-11-20 `
    -MaxSymbols 50 -Workers 1 -Attempts 1 `
    -BatchSize 10 -BatchPauseSeconds 5 `
    -MaxRateLimitErrors 2 -MaxConsecutiveFailures 10 `
    -SkipUniverseRefresh

$r = Get-Content local_data\us_equity_coverage.json -Raw | ConvertFrom-Json
$r | Select-Object cached_or_downloaded_symbols,missing_symbols,failed_this_run,download_status,stop_reason,provider_rate_limit_errors | Format-List
$r.failures.PSObject.Properties | Select-Object -First 10 Name,Value | Format-Table -Wrap
```

Settings can be changed after inspecting whether failures are true
HTTP 429/rate limits, invalid symbols, or legitimately absent history.
Continuing to retry during a confirmed rate limit is not a good
recovery strategy. Batch pause and stop thresholds **reduce** request
pressure; Yahoo can still throttle or deny access.

New download safety behavior:

- No more than \`--batch-size\` futures are submitted at once.
- \`--batch-pause-seconds\` delays the next batch.
- \`--max-rate-limit-errors\` stops when explicit 429/rate-limit
  errors accumulate; these are not retried immediately.
- \`--max-consecutive-failures\` stops if repeated missing/failed
  histories indicate a broken/provider-throttled run.
- A single **Ctrl+C** cancels unstarted requests in the current
  bounded wave. It can still take time for the 1–2 active Yahoo HTTP
  requests to finish, then writes partial price CSV and coverage JSON.
  The CLI exits code 130 after a user interrupt; the PowerShell script
  will **not** start the audit automatically on that interrupt.
- \`--cache-only\` assembles cached data without fetching market data,
  and runs the independent audit when invoked through the script.
- \`download_status=STOPPED_EARLY\` and \`stop_reason\` are diagnostic
  outputs, **never success or live trading permission**.

## Run locally on Windows PowerShell

Use your existing AtlasPM Windows repo; do not create a new repo.
First pull the PR branch and install the explicitly optional providers:

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git fetch origin
git switch --track origin/feature/selection-us-market-data-quality-v1
# If already created locally:
# git switch feature/selection-us-market-data-quality-v1

.\.venv\Scripts\python.exe -m pip install -e ".[market,calendar,ml]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_us_market_quality.py" -v
```

Next, **on a machine with public network access**, fetch and save the
latest current listing snapshot:

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli stock-universe `
    --output local_data\us_equity_universe.csv `
    --report local_data\us_equity_universe_report.json
```

For the first run, use a 2024 onward price window. As of **2026-11-22**,
the most recent completed NYSE session is **Friday 2026-11-20**.
\`--end\` is **EXCLUSIVE**, so use \`2026-11-21\` to include 11/20.
This example does not download 2000–2026 history for every company:

```powershell
# First smoke test: download or reuse cache for 50 names.
.\.venv\Scripts\python.exe -m atlaspm.cli stock-sync-market `
    --universe local_data\us_equity_universe.csv `
    --start 2024-01-01 --end 2026-11-21 `
    --max-symbols 50 --workers 2 --attempts 2 `
    --cache-dir local_data\us_equity_cache `
    --output local_data\us_equity_prices.csv `
    --report local_data\us_equity_coverage.json

# Run every remaining missing symbol; rerun with identical start/end
# to retry vendor failures without redownloading successful entries.
.\.venv\Scripts\python.exe -m atlaspm.cli stock-sync-market `
    --universe local_data\us_equity_universe.csv `
    --start 2024-01-01 --end 2026-11-21 `
    --max-symbols 0 --workers 2 --attempts 2 `
    --cache-dir local_data\us_equity_cache `
    --output local_data\us_equity_prices.csv `
    --report local_data\us_equity_coverage.json

$r = Get-Content local_data\us_equity_coverage.json -Raw | ConvertFrom-Json
$r | Select-Object listing_candidates,cached_or_downloaded_symbols,missing_symbols,coverage_pct,failed_this_run | Format-List
```

Do not mistake a cached symbol with any historical bars for a **valid
current-month candidate**. Actual as-of completeness is a separate question:

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli stock-market-audit `
    --universe local_data\us_equity_universe.csv `
    --prices local_data\us_equity_prices.csv `
    --as-of 2026-11-20 `
    --min-history-sessions 253 `
    --recent-sessions 63 `
    --min-recent-coverage-pct 85 `
    --min-month-end-coverage-pct 90 `
    --min-eligible-coverage-pct 75 `
    --min-eligible-names 500 `
    --output local_data\us_market_data_audit.json

$a = Get-Content local_data\us_market_data_audit.json -Raw | ConvertFrom-Json
$a | Select-Object market_data_status,listed_stock_universe_count,stocks_with_exact_decision_close,decision_close_coverage_pct,eligible_history_and_freshness,eligible_coverage_pct,manifest_age_at_decision_days | Format-List
$a.review_reasons
$a.missing_last_close_examples
$a.insufficient_history_examples
```

The audit exits with a readable research report even when status is
\`REVIEW_REQUIRED\`; it's a data QC finding, not a bug. JSON retains
eligible ticker identities and reason counts. Neither the file nor the
console output authorizes portfolio or broker decisions.

If Yahoo throttles or lacks certain new/illiquid tickers, rerun the
same quote window later. Failures are explicit. \`--max-symbols 0\`
attempts ALL remaining uncached names; the first batch of 50 is not a
full-market download. Re-running with a **different \`--end\`** currently
invalidates that symbol's previous exact-interval cache. This avoids
mixing adjusted prices from differently adjusted corporate-action
vintages. Efficient vendor-safe rolling updates require a separate
corporate-action-aware source strategy.

## How this interacts with monthly AI

The monthly model currently was trained on **38 surviving tickers** and
its training cutoff is 2018-12-31. **Do not** assume its
performance transfer to the newly downloaded US market. Merely
pointing a 38-ticker-trained model at thousands of symbols is not
validation. The \`stock-alpha-gate\` is intentionally fail-closed for
old models, unverified coverage and incomplete month-end sets.

Next stages after verifying these real data:
1. Obtain historical point-in-time listings, IPOs and delisted names
   from a properly licensed source, if possible.
2. Refit/compare stock-ranking models with a longer training window,
   purged labels and truly later out-of-sample months; separately
   capture adjusted OHLCV and publication-dated fundamentals.
3. Integrate the result into the existing monthly AI research pipeline
   and future research-only allowlist contract. **No trade connection.**

## Checks and audit semantics

\`stock-market-audit\` checks that \`--as-of\` is an actual NYSE session
and scans the CSV in one pass, preserving memory at approximately
O(number of securities + 63 recent sessions per security). It accepts
date-major or ticker-major files, but expects each ticker's own rows
to be chronologically increasing. Duplicate/out-of-order records
are counted and excluded from eligibility.

A ticker is date-qualified only with **an exact as-of close**, at least
\`--min-history-sessions\` valid historical records and
\`--min-recent-coverage-pct\` of the latest \`--recent-sessions\`
actual NYSE sessions. Future-dated quote records are ignored (counted
separately), not used for history/eligibility. Unknown symbols,
invalid adjusted closes, duplicates and recent off-calendar data
cause explicit review reasons. Manifest freshness and membership
are audited; a listing snapshot from far **after** an old historical
decision date is flagged. Date-bounded current membership is still
not an investable point-in-time historical universe.

\`READY_FOR_RESEARCH_REVIEW\` means only that specified data-quality
checks passed. It **never** means validated alpha, trade approval or
live execution permission. Private output stays under Git-ignored
\`local_data/\`, and \`live_execution_enabled=false\`.
