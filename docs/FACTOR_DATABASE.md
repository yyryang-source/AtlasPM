# AtlasPM unified equity factor mart V1

Research-only, local-first, source-isolated daily factor store on top of the
existing 5-minute Parquet lake and, optionally, the SQLite adjusted-close
warehouse. It **does not** download or alter prices, and is **not** an
approval gate for ML training, investment signals or live orders.

## First run on Windows PowerShell

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git switch main
git pull --ff-only origin main
$py = '.\.venv\Scripts\python.exe'
& $py -m pip install -e '.[lake]'
& $py -m unittest discover -s tests -p 'test_factor_db.py' -v

# While the full-market Hugging Face download is running, inspection is safe:
& $py -m atlaspm.cli market-db factor-catalog
& $py -m atlaspm.cli market-db factor-build --month 2026-03 --dry-run

# Only when 2026-03 source month's converted Parquet exists:
& $py -m atlaspm.cli market-db factor-build --month 2026-03 --source hf
& $py -m atlaspm.cli market-db factor-audit --month 2026-03
& $py -m atlaspm.cli market-db factor-query --symbol AAPL --start 2026-03-01 --end 2026-03-31 --limit 5
& $py -m atlaspm.cli market-db factor-status

# After multiple local months exist, process available months in order:
& $py -m atlaspm.cli market-db factor-batch --start-month 2000-01 --end-month 2026-03 --dry-run
& $py -m atlaspm.cli market-db factor-batch --start-month 2000-01 --end-month 2026-03
```

The batch selects only months with a local source file, but does not infer
missing months or verify historic exchange listings. For a first trial use
`--max-months 2`. A 36-calendar-month history window is used to compute
252-observation momentum; early months can have null long-lookback factors.
For tight RAM, pass `--memory 2048MB --threads 2`.

To compute a *separate, never blended* set of close-only features from
imported SQLite `equity_daily` records, use `--source sqlite`. No volume,
intraday, or adjustment-independent liquidity factor is fabricated for that
source. The two sources have different price conventions, so do not compare
or train across them without independent reconciliation and explicit policy.

## Catalog and schema

Keys: `source`, `session`, `symbol`, `trade_date` via partition path + row.
Daily *wide* Parquet stores close, observed volume/bar counts, known last-bar
timestamp, and 12 typed factor columns:

- momentum: `ret_1d`, `mom_5d`, `mom_21d`, `mom_63d`, `mom_126d`, `mom_252d`
- risk/trend: `volatility_21d` (unannualized), `price_vs_sma_20d`
- liquidity: `volume_ratio_20d`, `avg_dollar_volume_20d`
- price action: `intraday_return`, `intraday_range`

Missing observations are **never** filled. Each momentum lookback uses only
prior or completed-close observations and has an explicit maximum calendar
gap. Volatility requires 21 valid consecutive-observation daily returns;
volume ratio requires 20 prior observations. All factors are **end-of-day
only**, with vendor ingestion latency unverified. Intraday 5-minute price
adjustment and corporate-action semantics are not certified. `symbol` is not
an immutable historical security ID; historical universes are not PIT-safe.

Paths (always ignored by git):

```
local_data/factors/v1/daily/source=hf/session=rth/year=2026/month=03/daily_2026-03.parquet
local_data/factors/v1/factors/source=hf/session=rth/year=2026/month=03/factors_2026-03.parquet
local_data/factors/v1/factors/source=hf/session=rth/year=2026/month=03/factors_2026-03.json
```

Manifests include source family, schema version, prior daily file
fingerprint, row count, source adjustment limitations and fail-closed flags.
Interrupted builds use temporary files and atomic replacement. Existing
output is reused if its input fingerprint and schema version match;
`--refresh` forces re-materialization. If an **earlier month** changes,
rebuild that month and subsequent months so their 36-month history
fingerprints are checked again. Re-running the full batch is idempotent.

## Data rights and prohibited shortcuts

The HF feed is Finnhub-derived and rights are **not verified**. Public hosting
is not an open-data license. Do not upload Parquet, factor outputs, provider
prices, keys, or client data to GitHub. The warehouse makes no attempt to
bypass the existing HF QC warnings, split reconciliation, training gates,
broker margin checks or live-execution restriction. The 5m volume-derived
metrics are estimates based on the observed feed, **not** certified ADV.

Potential V2: explicit point-in-time security IDs, effective-dated
corporate actions/fundamentals, factor normalization within eligible dated
universes, independent daily benchmark reconciliation, and immutable dataset
snapshots for walk-forward experiments. None are implied by V1.
