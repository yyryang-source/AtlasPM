# Factor Evaluation V2 — exploratory, offline, not an alpha certificate

This module evaluates **materialized** V1 daily factors using synthetic-only
CI fixtures and optional local vendor-derived data. It never downloads
prices, modifies the source factor lake, places orders, trains models, or
automatically feeds factor results into stock selection.

## Windows PowerShell

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git switch main
git pull --ff-only origin main
$py = '.\.venv\Scripts\python.exe'
& $py -m pip install -e '.[lake]'
& $py -m unittest discover -s tests -p 'test_factor_eval.py' -v

# Calculate an evaluation plan; no SQL scans of historical prices:
& $py -m atlaspm.cli market-db factor-evaluate --start 2000-02-01 --end 2000-03-31 --factors mom_5d mom_21d --horizons 1 5

# Research-only, explicitly opt-in (sample likely too short for robust signals):
& $py -m atlaspm.cli market-db factor-evaluate --start 2000-02-01 --end 2000-03-31 --factors mom_5d mom_21d --horizons 1 5 --exploratory

# When contiguous months have been materialized, evaluate a longer interval:
& $py -m atlaspm.cli market-db factor-evaluate --start 2000-02-01 --end 2001-03-31 --factors mom_5d mom_21d volatility_21d --horizons 1 5 21 --holdout-start 2001-01-01 --exploratory
```

Run V1 factor-batch for the source months **and** later months needed to mature
the forward return labels. Missing/future not-yet-recorded dates are never
invented. Monthly source metadata are checked; a missing manifest, bad close,
or duplicate security/date fails closed. Memory defaults to 4GB and 4 DuckDB
threads; tune via `--memory 2048MB --threads 2`.

## Timeline and formulas

A factor at session T is assumed available **after T close**. Entry is the
next distinct observed market session **close** (T+1). For horizon h, exit
is session (T+h+1) close. The forward return is

`close(T+h+1) / close(T+1) - 1`.

This **does not** mean that an order can be filled at the exact next close;
slippage, auction availability, bid/ask spread, taxes, borrow and corporate
actions are **unverified**. A complete cross-stock *calendar date* grid is
used to align endpoints, not `lead(close)` over each ticker's sparse
observations. Gaps over seven calendar days anywhere in the label interval
disqualify the cohort. A missing entry/exit price excludes only that stock.
The historical ticker's stable security identity is **not** verified.

For h > 1, evaluate every h-th observed global session (index modulo h)
to avoid overlapping forward holding windows by construction. The phase
is fixed to offset zero; this is a diagnostic that needs multi-offset
robustness testing in future releases.

For each date/factor, eligible stocks are ranked with average ranks for tied
values. Rank IC = Pearson correlation of the factor ranks and the *future*
return ranks (Spearman). The report computes the mean daily Rank IC and
ICIR = mean(date-IC) / sample stdev(date-IC), **not annualized**.
Dates with constant factor/label produce NULL IC and are not scored.
Reported ICs are directional: a negative IC may be useful when a factor is
reversed, but no return claim is made.

Stocks are assigned to factor quintiles by percentile of their tied
factor ranks. Daily equally weighted Q1..Q5 mean future *h-session* returns
are summarized across dates. Q5−Q1 is an unlevered **spread statistic**,
not a realized long-short portfolio. The optional illustrative fixed cost
deducts **two per-leg round-trip costs** from the spread. This is *not* a
realistic net P&L; no portfolio equity curve or maximum drawdown is reported.

## Quality defaults

- Min 30 paired eligible stocks per scored date (`--min-symbols`).
- Min 8 dated valid IC observations (`--min-dates`), otherwise NULL summary.
- HF only: >=60 observed 5m buckets and >=85% minute-record coverage within
  those 5m buckets **at signal, entry, and exit** (`--min-bars 60`
  and `--min-minute-fraction 0.85`). This is a liquidity/data-quality
  *heuristic*, not proof of exchange-wide completeness. It excludes some
  legitimate half-days and illiquid securities, introducing selection bias.
- SQLite adjusted-close-only has no intraday coverage fields and must be
  evaluated **separately** (`--source sqlite`); no fake minute coverage.
- An optional `--holdout-start YYYY-MM-DD` splits chronological
  diagnostics. Choosing the holdout or factors after looking at historical
  results **is not** a genuine independent out-of-sample test.
- Missing trading sessions between selected months prevent crossing gaps.
  Early factor warmup NULLs are omitted, never imputed.

The full dated evaluation report is saved under gitignored
`local_data/factors/v2/evaluations/` and the CLI returns a compact
preview of the first 10 dated ICs. `--no-save` disables report persistence.
Data never leave the local machine during this evaluation.

## Non-negotiable restrictions

- The Hugging Face historical minute data are attributed to Finnhub and
  do **not** have independently verified redistribution or research rights.
- Corporate-action and split-adjustment behavior is unknown; 2000 stock
  tickers can refer to **different historical companies** than current tickers.
  Until security identity and true point-in-time eligibility are verified,
  correlations are exploratory only and prone to survivorship bias.
- Results are **never** marked approved for ML training or trading. IC, IR,
  quintiles, and "holdout" do not bypass these gates.
- For credible alpha claims a separate, genuinely unseen universe, independent
  return adjustment checks, higher-frequency execution simulation, uncertainty
  estimates, delisting handling, sector neutrality and capacity analysis are
  needed.

Interpret `INSUFFICIENT_DATED_IC` as "not enough evidence", not as zero
alpha. Interpret `EXPLORATORY_UNVERIFIED` as a *computed diagnostic*,
not as an investment grade or factor approval.
