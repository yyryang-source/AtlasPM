# US whole-market stock screener for AtlasPM (current listings)

This feature is an **all-current-US-exchange-listed stock discovery source** for
the existing `stock-screen` factor ranking. It is NOT another RiskNet,
RegimeNet, FusionNet, portfolio optimizer or broker execution system.
`live_execution_enabled` remains false.

## Windows PowerShell 5.1

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git fetch origin
git switch --track origin/feature/us-market-stock-universe-v1
# If branch exists: git switch feature/us-market-stock-universe-v1
.\.venv\Scripts\python.exe -m pip install -e ".[market]"

# Build the currently listed US equity candidate universe.
.\.venv\Scripts\python.exe -m atlaspm.cli stock-universe --output local_data\us_equity_universe.csv --report local_data\us_equity_universe_report.json

# Test 50 symbols, then resume ALL remaining symbols with no max limit.
.\.venv\Scripts\python.exe -m atlaspm.cli stock-sync-market --universe local_data\us_equity_universe.csv --start 2024-01-01 --end 2026-11-21 --max-symbols 50 --workers 2 --cache-dir local_data\us_equity_cache --output local_data\us_equity_prices.csv --report local_data\us_equity_coverage.json

.\.venv\Scripts\python.exe -m atlaspm.cli stock-sync-market --universe local_data\us_equity_universe.csv --start 2024-01-01 --end 2026-11-21 --max-symbols 0 --workers 2 --cache-dir local_data\us_equity_cache --output local_data\us_equity_prices.csv --report local_data\us_equity_coverage.json

# Audit failures instead of assuming full market coverage.
$r = Get-Content local_data\us_equity_coverage.json -Raw | ConvertFrom-Json
[PSCustomObject]@{
    Listings = $r.listing_candidates
    Covered = $r.cached_or_downloaded_symbols
    Missing = $r.missing_symbols
    CoveragePct = $r.coverage_pct
    FailedThisRun = $r.failed_this_run
} | Format-List

# Screen all SUCCESSFULLY downloaded stocks; output scores only, no trading.
.\.venv\Scripts\python.exe -m atlaspm.cli stock-screen --prices local_data\us_equity_prices.csv --as-of 2026-11-20 --top 50 --output local_data\us_market_top50.json
$r = Get-Content local_data\us_market_top50.json -Raw | ConvertFrom-Json
$r.selected | Select-Object rank,symbol,score | Format-Table -AutoSize
```

For 2000–2026 data change `--start 2024-01-01` to
`--start 2000-01-01` on BOTH sync calls, and use a new
`--cache-dir local_data/us_equity_cache_2000_2026`.
Full 26-year daily histories for thousands of tickers may be huge.
For current rankings the screener uses roughly the latest 253 sessions, so
you don't have to fetch 26 years to screen today's market.

## Coverage limitations

Listing sources are Nasdaq Trader daily directories:

- https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
- https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt

Explicit ETFs and test securities, plus obvious preferred shares, units,
warrants, rights and debt/funds, are filtered out. Ambiguous non-ETF names are
retained with `unverified_equity` classification for audit. US-listed foreign
issuers and ADRs may appear, so this is not restricted to US-incorporated firms.
The output preserves original and Yahoo-normalized symbols, exchange, issue
name and snapshot timestamp. Nasdaq directories are TODAY'S listings and do
not reconstruct 2000–2026 delisted companies or historical index membership.

Quotes use unofficial Yahoo/yfinance. Each successfully downloaded ticker is
cached separately with date bounds, then assembled into a streaming CSV.
Partial downloading with `--max-symbols` is intentionally reported as partial.
`--max-symbols 0` attempts every still-missing symbol. Failures are listed
in private coverage JSON; they are **not silently backfilled**. The provider
may rate-limit or lack prices. Cached symbols only match identical date bounds.

The screen is a ranking of what actually downloaded, not a claim that all
official listings were valid/covered. Retrospective adjusted prices are not
certified point-in-time data, and delistings are missing from this CURRENT
listing. Neither live nor paper trade execution is enabled. All output paths
stay under private gitignored `local_data/`.

## Regression tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_us_market_pipeline.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_stock*.py" -v
```
