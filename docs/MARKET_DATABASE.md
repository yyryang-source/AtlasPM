# AtlasPM local US market database (v1)

Research-only SQLite warehouse, built into `python -m atlaspm.cli market-db`.
**No broker orders, live feeds, licensed data, automatic download, or historical point-in-time certification.**
Real data stay on the **user's Windows machine**, under gitignored `local_data/`.
The first database is `local_data/atlaspm_market.sqlite3`; it will be empty until imports run.

## Windows PowerShell: use existing downloaded data

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

& $py -m atlaspm.cli market-db init
& $py -m atlaspm.cli market-db import-stocks --input local_data\us_equity_prices.csv

# Include the four 2026-11-23 separate daily-close CSVs, if downloaded:
& $py -m atlaspm.cli market-db import-stocks --glob "local_data/new_listing_*.csv"

& $py -m atlaspm.cli market-db status
```

The stock import accepts the existing `date,symbol,adjusted_close` output
of `stock-sync-market` and `download-prices`. These are adjusted **closes**,
not full stock OHLCV. Existing caches remain unchanged. Stock records from
2026-11-23 must not be treated as available on 2026-11-20.

## Options (from the existing Massive research download kit)

```powershell
# After obtaining actual option contract and candle CSVs:
& $py -m atlaspm.cli market-db import-option-contracts --input "local_data/options/massive/selected_contracts.csv" --as-of 2026-11-16
& $py -m atlaspm.cli market-db import-option-bars --glob "local_data/options/massive/1d/*.csv"
& $py -m atlaspm.cli market-db import-option-bars --glob "local_data/options/massive/5m/*.csv"
& $py -m atlaspm.cli market-db import-option-bars --glob "local_data/options/massive/1m/*.csv"
& $py -m atlaspm.cli market-db status
```

Option CSV schema matches the prior kit:
`symbol,bar,datetime_et,date_et,timestamp_ms,open,high,low,close,volume_contracts,vwap,trade_count,sma5,sma10,sma20,ema5,ema10,ema20,volume_ma20,consecutive_bars`.
Option contract IDs use OCC/Massive `O:...` format. **Import contract
reference snapshots before option candles**. A contract directory without
`reference_asof` is not an unbiased historical tradability universe.
Imported SMA/EMA represent the original candle timeframe and are **not**
silently recalculated or forward-filled. Bid/ask, Greeks and IV require
additional authorized inputs and are *not* falsely synthesized.

## US Treasury auctions

```powershell
# Use whichever downloaded auction CSV prefix exists:
& $py -m atlaspm.cli market-db import-treasury --glob "local_data/bonds/treasury/auctions_*.csv"
& $py -m atlaspm.cli market-db status
```

The official FiscalData auction CSV should contain `cusip`, `security_type`,
`security_term`, `interest_rate`, `auction_date`, `issue_date`,
`maturity_date`, `high_yield`, and `offering_amt`.
Missing CUSIPs are skipped and counted, not fabricated. Treasury auction
yields are **not** secondary-market executable bids or ask prices.
Corporate bonds, municipal bonds and full Treasury yield curves remain
future data adapters; table `bonds` currently contains Treasury securities.

## Reserved future datasets

Tables `option_quotes`, `option_greeks`, and `bond_quotes` are created but **have no importer in v1**. They are intentionally empty until licensed historical bid/ask, Greeks/IV, and secondary-market bond price/yield data adapters are implemented and validated. Neither synthetic Greeks nor fabricated bond prices are written.

## Guarantees and limitations

- Re-importing the same CSV upserts by unique instrument/timeframe/timestamp;
  rows are not duplicated; report `rows_processed` is not a new-row count.
- A malformed CSV row rolls back *that entire file's* import.
- `market-db status` reports stored rows, not market eligibility.
- SQLite works for local daily equities and a bounded set of option bars.
  For all-market historical OPRA 1-minute files, plan partitioned Parquet
  backed by DuckDB and licensed bulk datasets.
- Never commit `local_data/`, API keys, or personal brokerage exports.
- No automatic model approval, order handoff, risk gate bypass or real trade.

## Download missing Treasury and options data (Windows PowerShell)

The warehouse does **not** download market data merely because tables exist.
The first version of these commands uses official Treasury public API and
licensed Massive endpoints. All downloaded CSVs remain under ignored
`local_data/`; API keys are read from the local environment only.

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

# Download official Treasury auctions; page cap 0 means all available pages.
& $py -m atlaspm.cli market-db fetch-treasury --max-pages 0
& $py -m atlaspm.cli market-db import-treasury --glob "local_data/bonds/treasury/fiscaldata_v2/auctions_*.csv"

# If you have a Massive Options plan and key (never send it to a chat):
$secret = Read-Host "Massive API Key" -AsSecureString
$env:MASSIVE_API_KEY = [System.Net.NetworkCredential]::new("", $secret).Password

# Download 20 contracts each for SPY and QQQ at the historical as-of snapshot.
& $py -m atlaspm.cli market-db fetch-option-contracts --underlyings SPY QQQ --as-of 2026-11-20 --expiry 2026-12-18 --max-contracts 20 --rpm 5
& $py -m atlaspm.cli market-db import-option-contracts --input local_data/options/massive/selected_contracts.csv --as-of 2026-11-20

# Download daily + 5-minute candles for first 10 selected contracts.
& $py -m atlaspm.cli market-db fetch-option-bars --start 2026-11-16 --end 2026-11-20 --timeframes 1d 5m --max-contracts 10 --rpm 5
& $py -m atlaspm.cli market-db import-option-bars --glob "local_data/options/massive/1d/*.csv"
& $py -m atlaspm.cli market-db import-option-bars --glob "local_data/options/massive/5m/*.csv"
& $py -m atlaspm.cli market-db status

# Clear the key from the current PowerShell session.
Remove-Item Env:MASSIVE_API_KEY
```

**Limits:** The Massive API must be accessible under your actual account
entitlements; HTTP 401/403/429 exits safely. Bar requests are limited to
selected contracts and dates, not all US options or all expired contracts.
An empty bar window generates no file rather than a fabricated zero-price bar.
Daily moving averages use daily bars; 5-minute averages reset across time gaps.
These trade aggregates are *not* executable bid/ask quotes. For full US OPRA
minute history prefer appropriately licensed daily flat files and a partitioned
Parquet/DuckDB data lake rather than millions of single-contract API calls.


## Full US stock 5-minute Parquet lake (Massive, 2003–2026)

Important limits: Massive's US Stocks SIP aggregate archive starts
2003-09-10, not in 2000. No prices from 2000-01-01 to 2003-09-09 are
silently invented. Massive's Basic free Stocks plan does not include
Flat Files; Starter/Developer/Advanced have distinct historical windows.
Each trading-day flat file covers **all stocks**, including securities that
are now delisted, not merely AtlasPM's 5,831 currently listed stocks.

The existing Massive REST API key can verify one day of Stock 5-minute
access, but Flat Files use a **different S3 Access Key and Secret Key**
provided by Massive Dashboard. Do not send either credential to anyone
or commit them to GitHub.

Windows PowerShell from the project root:

    cd C:\Users\Kevin\Desktop\AtlasPM
    git switch main
    git pull --ff-only origin main
    $py = ".\.venv\Scripts\python.exe"

    # Test one ticker/date with your existing session MASSIVE_API_KEY.
    & $py -m atlaspm.cli market-db stock-5m-probe --symbol AAPL --date 2026-11-20

    # Plan the complete request without downloading or S3 credentials.
    & $py -m atlaspm.cli market-db stock-5m-backfill --start 2000-01-01 --end 2026-11-23 --dry-run

    # For actual bulk ingest: install AWS CLI and configure separate S3 keys.
    winget install --id Amazon.AWSCLI -e
    aws configure --profile massive
    # Supply your S3 Access Key, Secret Key, region us-east-1, output json.
    & $py -m pip install -e ".[lake,calendar]"

    # Start with only 2 sessions; inspect storage and speed.
    & $py -m atlaspm.cli market-db stock-5m-backfill --start 2026-11-19 --end 2026-11-20 --max-days 2 --profile massive
    & $py -m atlaspm.cli market-db stock-5m-status

    # Only once credentials, storage, and short test are verified:
    & $py -m atlaspm.cli market-db stock-5m-backfill --start 2000-01-01 --end 2026-11-23 --profile massive

The downloader uses the official NYSE calendar for real backfills, clips
requested dates to 2003-09-10, and fetches the daily market-wide raw gzip
source us_stocks_sip/minute_aggs_v1/YYYY/MM/YYYY-MM-DD.csv.gz.
It converts each day to a ZSTD Parquet file under:
local_data/stock_5m/parquet/session=rth/year=YYYY/month=MM/YYYY-MM-DD.parquet

Default session is regular trading hours, 09:30–16:00 America/New_York;
specify --session all to preserve pre/post-market. Each resulting bar
stores symbol, trade_date_et, timestamp_ms (UTC milliseconds), earliest
minute open, highest high, lowest low, latest close, summed volume and
trades, and source_minute_bars (actual constituent minutes). Missing
minute bars are NOT forward-filled. Prices are raw/unadjusted; do not
compare directly to yfinance auto-adjusted historical closes.

All files are private under gitignored local_data. The downloader
skips complete Parquet date files on rerun; it keeps original source
gzip files by default. --remove-raw deletes an original gzip only
AFTER its Parquet output is successfully generated. Missing or not-yet-
published exchange-day files are reported; they are not counted covered.
The 2026-11-23 flat file is ordinarily published about 11 AM ET on
2026-11-24, so it may not be available on Nov 23.

Large-scale minute data do NOT go in AtlasPM's SQLite database; DuckDB
can query the Parquet date partitions directly. Historical delisted
tickers in files do not by themselves prove complete point-in-time
eligibility metadata. Expect large network traffic and storage.

Example Python query (after Parquet files have been created):

    import duckdb
    db = duckdb.connect()
    data = db.execute(
       "SELECT trade_date_et,timestamp_ms,open,high,low,close,volume_shares "
       "FROM read_parquet("
       "'local_data/stock_5m/parquet/session=rth/year=*/month=*/*.parquet') "
       "WHERE symbol='AAPL' ORDER BY timestamp_ms LIMIT 20"
    ).fetchall()
    print(data)



## Alternate opt-in public-hosted US stock minute archive (rights unverified)

Hugging Face 'mito0o852/OHLCV-1m' is exposed as a separate monthly 1m-to-5m
Parquet research adapter. It does not replace Massive, change daily adjusted-close
research, or enable IBKR/live orders. See [PowerShell, data-rights warning and
schema details](HF_STOCK5M.md). Public hosting does not establish an open-data
license for its Finnhub-derived historical prices.
