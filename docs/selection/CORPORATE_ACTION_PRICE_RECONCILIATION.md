# Verified reverse-split audit & non-destructive research-copy reconciliation

## Why this is needed

On Nov 22, 2026, the user's full-current-listed US stock ranker
returned **test IC -0.01774** versus simple 12–1 momentum IC +0.11176,
and neither validation nor test had an unambiguous V2 Top 10.

Ticker-level momentum forensics identified extreme momentum and large
subsequent declines. The user's **actual local** Yahoo history for DUKR
showed:

| Date | Yahoo Close | Yahoo Adj Close | Yahoo Stock Splits | Volume |
| --- | ---: | ---: | ---: | ---: |
| 2026-03-05 | 0.300 | 0.300 | 0 | 120,700 |
| 2026-03-06 | 7.660 | 7.660 | 0 | 100 |

The close-only jump looks like +2,453.33%, but the official
[DUKE Robotics SEC Form 8-K](https://www.sec.gov/Archives/edgar/data/1638911/000121390026024093/ea0279505-8k_duke.htm)
confirms a **1-for-25 reverse split on 2026-03-06**.
The comparable prior price is approximately $0.30 × 25 = $7.50,
yielding a normalized move of approximately **+2.13%**.
Yahoo's split actions field being zero is a missing provider action,
not evidence of a 2,453% investment gain.

Ticker DUKR also had 594 earlier (OTC/pre-Nasdaq) observations in the
user's local file; the company only began Nasdaq trading on 2026-05-15.
Those observations are not necessarily wrong, but **current Nasdaq
membership does not imply historical Nasdaq membership**. An
exchange-membership/volume gate remains a separate unimplemented task.

Other demonstrated event windows:
- DKI 2026-10-21 $2.00 -> 2026-10-22 $2.25; Yahoo split field 0.02
  for the 1-for-50 reverse split. Looks already adjusted.
- AMTD 2026-10-16 $29.88 -> 2026-10-19 $28.50; Yahoo split field
  ~0.016667 for a 1-for-60 ADS ratio change. Looks already adjusted.
**Do not apply another split factor** to already-adjusted prices.

## Local PowerShell workflow

This command performs **no external network access**. Only the SEC
source URL string and verified split date/ratio from the GitHub-committed
manifest are used. It does not download or validate SEC filing bytes
during execution. Humans must verify manifest provenance independently.

First inspect the raw history. Do NOT touch its 3.8M bars.

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git pull --ff-only origin main

.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_corporate_action_reconciliation.py" -v

.\.venv\Scripts\python.exe -m atlaspm.cli stock-split-reconcile `
    --prices local_data\us_equity_prices.csv `
    --manifest docs\selection\verified_reverse_splits_2026.json `
    --report local_data\verified_split_audit.json

$a = Get-Content local_data\verified_split_audit.json -Raw | ConvertFrom-Json
$a.event_checks | Format-List
```

Expected for the actual 2026 raw dataset:
- DUKR date 2026-03-06, ratio 25: `LIKELY_MISSING_ADJUSTMENT`
- `raw_close_multiplier` ≈ 25.533333
- `normalized_close_return_if_split` ≈ 0.021333
- `REVIEW_REQUIRED` until the adjustment is reviewed

Only after verifying the audit, generate a **new and distinct** corrected
research dataset:

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli stock-split-reconcile `
    --prices local_data\us_equity_prices.csv `
    --manifest docs\selection\verified_reverse_splits_2026.json `
    --apply-confirmed `
    --output local_data\us_equity_prices_verified_splits.csv `
    --report local_data\verified_split_application.json
```

This will refuse to overwrite an existing output or the original
`us_equity_prices.csv`. If `local_data/us_equity_prices_verified_splits.csv`
already exists, don't delete it blindly; inspect versions and hashes first.

A single verified 1-for-25 event adjusts **only DUKR prices before
2026-03-06**, multiplying them by 25 so every price in that ticker's
series is expressed in post-split share units. It leaves the other
5,829 symbols untouched. The original private CSV and its cache remain
unchanged. The resulting JSON records input/output SHA256 hashes and
how many rows were changed.

Sanity-check the corrected copy with the *same* audit manifest:

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli stock-split-reconcile `
    --prices local_data\us_equity_prices_verified_splits.csv `
    --manifest docs\selection\verified_reverse_splits_2026.json `
    --report local_data\verified_split_postcheck.json

$b = Get-Content local_data\verified_split_postcheck.json -Raw | ConvertFrom-Json
$b.event_checks | Format-List
```

Expected status: `NO_CORRECTION_INDICATED`, DUKR classified
`ALREADY_ADJUSTED_OR_NORMAL_PRICE`.

**Do not retrain and announce improvement** on the already-reviewed
2026 holdout merely because a price adjustment changed IC. Once
normalization is verified, compare the old/new *historical research
diagnostics* with identical cohorts and preserve both versions. The
existing research-only stock and IBKR live-trading gates remain closed.

## Safety limitations

- The 20% matching window around a verified split ratio is only
  corroboration of an apparent missing adjustment, not independent
  proof of correct Yahoo prices or split identity.
- `Stock Splits` from Yahoo can be incomplete and the CSV contains
  prices, not historical liquidity, market caps, shares outstanding,
  actual order execution, or historical listing identity.
- This tool does not automatically infer splits from spikes, add
  guessed action dates, or silently clean other tickers.
- `ALREADY_ADJUSTED_OR_NORMAL_PRICE` only means a date-to-date
  multiple close to one; confirm corporate-action accounting and
  timestamps separately.
- Large legitimate gains/losses are not errors and should not be
  suppressed merely to improve strategy metrics.
- Full 2000–2026 point-in-time member histories, delistings and
  untouched forward test periods are still missing.
- Only research files under Git-ignored `local_data` are created;
  never commit full private quote sets, credentials or broker snapshots.
