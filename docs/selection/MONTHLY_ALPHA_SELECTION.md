# AtlasPM Monthly Alpha Selection Engine — stock picker ONLY

## Definition

This is a **monthly AI stock research publication**. It does NOT trade, size
positions, decide leverage, manage risk, connect brokers or trigger portfolio
rebalancing. The existing AtlasPM quant/risk platform remains separately
responsible for those tasks. The monthly report is NOT execution authorization:
\`automated_handoff_approved=false\`, \`live_execution_enabled=false\`,
\`order_instructions=[]\`.

Suggested cadence:

- **Daily, after market close:** an independent feed updates adjusted prices
  and future OHLCV/fundamental data. This first monthly module does NOT
  automatically refresh the source data or train a model. Missing month-end
  quotes cause rejection, not a stale recommendation.
- **Monthly:** after the *final actual NYSE trading session* of the month,
  no earlier than 17:00 America/New_York, the engine ranks all stocks
  present in its input file with the V2 groupwise LightGBM model.
  It publishes Top 20 and up to Top 50 with an explicit model version,
  date, audit reasons and score ties.
- **Quarterly review:** model retraining and validation are a separately
  approved **manual research procedure**. Models older than the configured
  training-age threshold are flagged REVIEW_REQUIRED.
- **Following month:** a separate quant/risk process may later consume a
  human-reviewed candidate list; no automatic handoff or order creation.

The NYSE holiday calendar uses \`pandas_market_calendars\` (optional
dependency); weekends, Good Friday and exchange holidays are not
approximated as normal weekdays. The system fails closed if the target
month is not finished or the last NYSE session is absent from the SPY CSV.

## Windows PowerShell 5.1

First, switch to the new GitHub branch and install the optional calendar
and ML packages:

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git fetch origin
git switch --track origin/feature/monthly-ai-stock-selection-v1
# If the branch is already tracked locally, use:
# git switch feature/monthly-ai-stock-selection-v1
.\.venv\Scripts\python.exe -m pip install -e ".[ml,calendar]"

.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_monthly_stock_selection.py" -v
```

**Current date November 22, 2026:** November's final NYSE trading day is
Nov 30, 2026, which has NOT occurred yet. Therefore test the completed
**October 2026** calendar-month snapshot on currently available private
38-ticker research prices (no retraining required):

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli stock-ai-monthly --prices local_data\us_stocks_2000_2026.csv --benchmark-prices local_data\market_2000_2026.csv --model local_data\atlas_stock_ranker_v2.txt --month 2026-10 --min-scored 20 --output-dir local_data\monthly_stock_research

$r = Get-Content local_data\monthly_stock_research\2026-10_monthly_stock_research.json -Raw | ConvertFrom-Json
$r.report_status
$r.review_reasons
$r.data_quality | Format-List
$r.top_20.candidates | Select-Object rank,symbol,ranking_score,tie_count | Format-Table -AutoSize
```

Expected result is **REVIEW_REQUIRED**, not a verified investment signal,
because the existing V2 model was trained through December 2018, only
38 stocks were supplied, Top 50 cannot be complete, and an all-US listing
manifest was not provided. It may also be flagged for any score ties.
\`2026-11\` must fail until the actual last session on Nov 30 after 17:00
ET **and** after the daily source has incorporated that final session.

To assess actual coverage against the discovered US listing directory,
add \`--universe local_data\us_equity_universe.csv\`. If only 38 price
tickers were downloaded, coverage should be low; the engine checks
actual matched ticker identities against the manifest, not a guessed
total. A full-US market rank requires a larger, properly downloaded
and validated local price dataset. It never silently labels 38 names
as "all of US stocks".

All research outputs are within gitignored local_data:
- YYYY-MM_monthly_stock_research.json — provenance, quality gates,
  Top20/50, score ties, model SHA and explicitly disabled execution.
- YYYY-MM_monthly_top50.csv — candidate rankings for offline inspection.

An existing month is never silently overwritten; specify \`--overwrite\`
only when intentionally replacing an old research snapshot.

## Optional local first-of-month scheduled runner

A ready-to-run **Windows PowerShell script** is included:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Invoke-MonthlyStockResearch.ps1 -Month 2026-10
```

If \`-Month\` is omitted, it targets the **previous calendar month**.
It uses local_data prices and the previously trained V2 model, and exits
nonzero if data are incomplete or reports already exist.
It **does not** download prices, retrain models, or place orders.

You may manually schedule this script for the first calendar day of
each month **after** your independently scheduled market data refresh
has finished, e.g. local 20:00. Task Scheduler installation is NOT
performed automatically in this PR. Be careful with a November 30
after-close holiday schedule and with upstream Yahoo market-data delays.

## Review gates

- Exact last NYSE closing session available for SPY; cannot rank on a
  convenient earlier Friday if the last day of the month is Monday.
- Current time after 17:00 NY time on the actual last session.
- Only scores stocks with a closing price on that decision session and
  sufficient trailing daily prices.
- Model training age >365 calendar days → STALE_MODEL warning.
- Below 20 stock histories → INSUFFICIENT_SCORED_STOCKS.
- No listing manifest → UNVERIFIED_FULL_US_MARKET_SCOPE.
- Manifest coverage <90% or mismatched ticker identities →
  INSUFFICIENT_MARKET_COVERAGE or SYMBOLS_OUTSIDE_MANIFEST.
- Top 20/50 cut through a score-tie group → TOP20/50_AMBIGUOUS.
- Fewer than 50 scoreable names → TOP50_INCOMPLETE.

Any review issue sets report_status REVIEW_REQUIRED. Even when no issues
exist, the most permissive status is **READY_FOR_RESEARCH_REVIEW**,
not approval for trading. Rank score = **ordinal, NOT projected % return**.

## Limitations

V2 historical test on 38 surviving incumbents remains selection-biased;
the model has not been proven on the all-US universe, small caps,
delisted names, or a completely untouched future holdout. An exchange
listing CSV is TODAY'S symbol set and cannot support PIT historical
research without IPO/delisting membership. The underlying Yahoo adjusted
CSV may be retrospectively revised. Daily quote ingestion, PIT financial
data, true full-market streaming training and quarterly retraining
automation are separate future work, and are not falsely claimed here.
