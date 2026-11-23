# AtlasPM 20-signal / five-family stock-screening research V1

**Status: 2026-11-23, original offline research code, no execution, no
promised profitability.** AtlasPM's existing ~5,830-current-listed-US-stock
2024–2026 price CSV is sufficient for **20 price-only voting signals**;
it is **not** sufficient to reproduce the complete capabilities of 22
different investment programs. This project **does not vendor, import, copy,
execute, or train** the projects below; it studies their public approach
and reimplements a compact, independently developed baseline. Never
conflate “researched 22 repositories” with “integrated 22 pretrained models.”

## Source projects (22 independently identified, public code + declared license)

GitHub repository metadata and project descriptions were checked
2026-11-23. This is a **research reference catalog**, not an endorsement
or a performance ranking. Repository stars/marketing are **not** audited
profitability. Licenses are repo-level declarations at review time and
must be checked again before any code reuse (none here). The important
distinction is whether each project is a screener, a feature toolkit,
or an evaluation/trading framework.

| # | Project (official GitHub URL) | Declared license | Public design idea reviewed | AtlasPM V1 relation |
| --- | --- | --- | --- | --- |
| 1 | [Microsoft Qlib](https://github.com/microsoft/qlib) | MIT | Factor research/model pipeline, features, reproducibility | price factor matrix; not Alpha158/Alpha360 clone |
| 2 | [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | MIT | research environment and train-test separation | future comparison only; no RL agent deployed |
| 3 | [QuantConnect LEAN](https://github.com/QuantConnect/Lean) | Apache-2.0 | universe and trading-session discipline | original research-only point-in-time checks |
| 4 | [Zipline Reloaded](https://github.com/stefan-jansen/zipline-reloaded) | Apache-2.0 | event/portfolio simulation discipline | future costed forward testing, not copied |
| 5 | [Alphalens](https://github.com/quantopian/alphalens) | Apache-2.0 | factor quantile and IC analysis | factor percentiles, no unearned IC claim |
| 6 | [Alphalens Reloaded](https://github.com/stefan-jansen/alphalens-reloaded) | Apache-2.0 | factor/turnover/IC diagnostics | future independent IC/turnover report |
| 7 | [TradingView-Screener](https://github.com/shner-elmo/TradingView-Screener) | MIT | multi-field technical/fundamental filters | source-independent local signal catalog, no TV scraping |
| 8 | [Crible](https://github.com/maxgfr/crible) | MIT | transparent fundamental ratios and provenance | future published fundamental snapshot, *not available* |
| 9 | [JhonHarry stock_screener](https://github.com/JhonHarry/stock_screener) | MIT | value, quality, growth, momentum score pillars | family scoring concept; fundamentals pending |
| 10 | [meet-minimalist stock-screener](https://github.com/meet-minimalist/stock-screener) | MIT | relative strength, trend, multi-stage gates | momentum + trend families and explicit prefilters |
| 11 | [intelligent-stock-screener](https://github.com/MichaelMW/intelligent-stock-screener) | MIT | multiple presets (value, growth, momentum, low-vol) | style diversity and defensive family |
| 12 | [Daniel stock-screeners](https://github.com/danielkim009-cmd/stock-screeners) | MIT | Minervini/Turtle trend, breakout, indicator agreement | moving-average trend/52-week proximity |
| 13 | [neurobloom market-tools](https://github.com/neurobloomai/market-tools) | MIT | technical setup, relative-strength research, scoring | technical family scores and explainability |
| 14 | [kuant-core](https://github.com/zwmjj/kuant-core) | MIT | factor library and walk-forward controls | modular factor registry; walk-forward still missing |
| 15 | [lit26 Stock_Screener](https://github.com/lit26/Stock_Screener) | MIT | independent fundamental + technical views | separation; only price signals available |
| 16 | [bukosabino ta](https://github.com/bukosabino/ta) | MIT | technical feature taxonomy | basic original MA/trend/vol calculations, no import |
| 17 | [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) | BSD-3-Clause | downside risk, drawdown | defensive *ranking* only, no portfolio optimizer |
| 18 | [skfolio](https://github.com/skfolio/skfolio) | BSD-3-Clause | train/test separation and risk features | policy boundary with AtlasPM risk stack |
| 19 | [quant-trading](https://github.com/je-suis-tm/quant-trading) | Apache-2.0 | classic price trend/reversion examples | price-only trend/reversion families |
| 20 | [Bellwether stock-screener](https://github.com/unmiltambe/stock-screener) | MIT | transparent stock score and attribution | per-family score breakdown |
| 21 | [stoneidev stock-screener](https://github.com/stoneidev/stock-screener) | MIT | stage-two trend and screening gates | SMA trend plus entry-data quality checks |
| 22 | [StockIQ](https://github.com/Mamun/stockIQ) | MIT | multi-module screener, technical/value categories | extensible design; no AI claims imported |

Several more public GitHub repositories were reviewed but had **no
declared repository-level OSI license** when checked. Do **not** treat
their public code as permission to copy: rom-orlovich/stock-screener,
dedlydilip/equity-screener, DonnyWu/StockSeeker,
KarneeshkarV/screener, Justme-Cliff/Investing-ML,
alanhewenyu/value-screen, DogInfantry/sellside-research-engine.
These are not counted among the 22 licensed projects above.

## Actually implemented: 20 original price-derived signals, 5 non-independent families

Only historical closes **through the exact decision date** enter scores.
253 trading sessions are needed per symbol. Cross-sectional factor
percentiles use midranks (equal scores stay tied); each family combines
its own members equally and is **re-ranked** cross-sectionally so a
family with 5 correlated signals cannot dominate merely through count.
Final score = weighted sum of the five family midrank percentiles * 100.

| Family (fixed research weight) | Signals actually computed |
| --- | --- |
| Momentum (30%) | 12–1 month momentum, 6–1, 3–1, past 63-session total momentum |
| Trend (25%) | Price/SMA200, Price/SMA100, Price/SMA50, SMA50/SMA200, proximity to 253-session high |
| Defensive (20%) | Negative annualized 63- and 21-session volatility, negative downside volatility, 252-session max drawdown, worst 63-session daily return |
| Mean reversion (10%) | Past 5-session pullback, 21-session pullback, distance below 20-session high |
| Consistency (15%) | Positive daily return fraction over 63 and 126 sessions; directional path-efficiency ratio over 63 sessions |

**20 signals are highly correlated, not 20 independent edges**.
Weights and parameter values are fixed exploratory assumptions, NOT
selected on the already-inspected 2026 holdout. No value, quality,
sector-neutral, profitability, analyst, dividend, intraday/OHLC,
volume or liquidity factor is silently invented from closing prices.

- Hard research input requirements: exact date close, >=253 close
  observations, no gaps over 14 calendar days, recent 253-session span
  <=420 calendar days.
- Default exploratory screen price >= $5 (adjusted close); this is
  not actual tradability/dollar volume. Configurable.
- If consecutive adjusted-price ratio >=4 or <=1/4 anywhere within
  the 253-close lookback, quarantine and report name as
  `SUSPECT_CORPORATE_ACTION_OR_EXTREME_JUMP`. **Not proof** of bad
  vendor quotes; legitimate extreme returns can also trigger this.
  No split is silently repaired. DUKR's previously demonstrated
  missing 1:25 reverse-split adjustment should be corrected separately
  via AtlasPM's `stock-split-reconcile`, with the original preserved.
- Optional `--universe` restricts to the explicitly supplied CURRENT
  listing manifest; it does NOT create historical delisting membership.
- A tie at exact Top-N score precision yields `selected=[]` rather
  than arbitrarily choosing alphabetically. A separate preview is
  clearly labelled **not trade instructions**.
- Always outputs `status=REVIEW_REQUIRED`,
  `alpha_validated=false`, `research_only=true`,
  `live_execution_enabled=false`,
  `automated_handoff_approved=false`. Success here **never**
  turns on IBKR or sends candidate trades to AtlasPM's risk stack.

## Windows PowerShell — test immediately on your existing CSV

No API keys, vendor calls, new libraries, large extra downloads or model
training required. Run from your existing project folder after the PR is
merged and updated locally.

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git pull --ff-only origin main

.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_consensus_20_screener.py" -v

.\.venv\Scripts\python.exe -m atlaspm.cli stock-consensus-20 `
    --prices local_data\us_equity_prices.csv `
    --universe local_data\us_equity_universe.csv `
    --as-of 2026-11-20 `
    --top 10 `
    --min-price 5 `
    --max-daily-ratio 4 `
    --output local_data\consensus_20_2026-11-20.json

$r = Get-Content local_data\consensus_20_2026-11-20.json -Raw | ConvertFrom-Json
$r | Select-Object as_of,status,n_signals,n_families,top_n_unambiguous | Format-List
$r.data_quality | Format-List
$r.selected | Select-Object rank,symbol,adjusted_close,consensus_score,family_top_quintile_agreement_count | Format-Table -AutoSize
$r.review_reasons | ForEach-Object { Write-Host $_ }
```

On the original uncorrected CSV, DUKR might be quarantined due to its
unadjusted 2026-03-06 1:25 reverse-split jump. On the verified split
research **copy** (if locally generated) use
`--prices local_data\us_equity_prices_verified_splits.csv`.
Do NOT delete the raw file or confuse different price versions.
The tool intentionally refuses to report `READY` or execution
eligibility simply because the screen returns ten stocks.

## Required next steps before any claims

1. Verify 2024–2026 raw and corrected price lineage, full corporate
   action handling (not just DUKR) and historical stock ID mapping.
2. Add daily volume, average dollar volume, IPO/delist and exchange
   eligibility **as known at each historical date**.
3. Add trustworthy point-in-time published fundamentals to unlock
   independently tested value/quality/growth families; never mix
   current valuation with historical decisions.
4. Compare candidate sets against naive momentum, random/sector-matched
   baselines on **previously untouched** future dates, using walk-forward
   purges, transaction costs, turnover and drawdown. No reuse of the
   previously observed 2026 period for final evidence.
5. Leave live execution disabled and require independently approved
   broker risk/financing/margin controls if ever used beyond research.

Open-source license catalog is attribution for conceptual research
only; this code was authored independently and has **no added package
dependencies or external providers**.
