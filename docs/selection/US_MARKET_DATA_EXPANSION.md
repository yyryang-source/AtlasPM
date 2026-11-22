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
