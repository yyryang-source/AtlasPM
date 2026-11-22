# Institutional Portfolio Ledger (v0.4.1)

AtlasPM now includes a read-only cash and margin accounting contract for long stocks and ETFs, US Treasury securities and signed option contract positions.

This is a local synthetic research prototype: no brokerage connection, live quotes, account credentials, real orders, coupon transfers or option exercise is implemented.

## Windows PowerShell

~~~powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli ledger-report --file examples\institutional_ledger.json --output local_data\institutional_ledger_report.json
~~~

The example contains entirely fictional accounts and broker-sourced fields. The CLI prints a compact account summary and saves all instrument marks, warnings and reconciliation details to local_data/institutional_ledger_report.json. The local_data folder is excluded by Git.

## Valuation conventions

USD only. Missing currency data and FX other than USD are rejected; unsupported cases must not be silently converted.

| Asset | Signed market value calculation | Limits |
|---|---|---|
| Long stock or ETF | shares x mark_price | no short stocks, dividends, corporate actions or tax lots |
| Long US Treasury | face_value x (clean_price_per_100 + accrued_interest_per_100) / 100 | no day-count or coupon event engine, no verified CUSIP security master |
| Listed option | signed contracts x multiplier x mark_premium | no exercise, expiry, margin, full Greeks or OCC contract identity verification |
| Account equity | cash + receivables + signed asset marks - payables - margin debt - accrued financing interest | broker NLV reconciliation remains mandatory |

For example, a $100,000 face Treasury marked at 97.50 clean and $0.40 accrued per $100 face has a dirty market value of $97,900 ($97,500 clean + $400 accrued). The $400 must NOT be booked again as cash or an additional coupon receivable.

A short option marked -$800 is a liability. Its market value is NOT the maximum loss, margin requirement or option premium received. Contract multipliers are explicit and validated.

## Reconciliation and account controls

The ledger compares calculated NLV with an optional supplied broker_reported_net_liquidation field. Missing data or a difference beyond the user-supplied tolerance prevents readiness for risk review. Reconciliation in synthetic examples does NOT authenticate the user-provided broker figure.

Cash accounts cannot have financing debt, accrued margin interest or short option positions in this prototype. IDs must be unique within the account. Negative prices, invalid dates, impossible signed contract counts, stale quotes and stale account snapshots are rejected or flagged as appropriate.

The reporting-only US Treasury collateral tally only credits positions that explicitly have ELIGIBLE broker-confirmed status, source=broker and a nonnegative amount not larger than current dirty market value. UNKNOWN or INELIGIBLE receive zero credit. This tally is NOT included in cash, equity or buying power.

Each account has its own independent risk-readiness assessment. Consolidated net liquidation is additive for reporting, but no cross-account collateral or financing netting is assumed. A short option position needs additional broker margin review even if the account equity reconciles.

## Treasury DV01

If a modified duration is provided, approximate first-order DV01 is:

~~~text
Treasury DV01 (USD per 1bp) = dirty market value * modified duration * 0.0001
~~~

This is a linear approximation, not bond-specific yield curve pricing or regulatory margin validation.

## Relationship to central risk controls

The existing institutional-check policy gate is separate from this read-only ledger. A reconciled account value does NOT by itself authorize adding leverage, opening options or using US Treasury holdings as collateral. Real order-previews and current broker buying power are required in the eventual connected system.

## Remaining engineering requirements

Coupon and maturity accrual processing, corporate actions, tax lots, settlement status, authentic CUSIP/OCC identities, point-in-time marks, real broker reconciliation, options Greeks and assignment, approved paper OMS and secure broker connectivity remain future tasks. No broker API keys or actual account data should be committed to public GitHub.
