# AtlasPM v0.4.6 — Imported Broker Snapshot Reconciliation

**Read-only, local-only bridge toward a real broker adapter.** This version compares the existing multi-asset ledger with a separately prepared broker export expressed in a normalized JSON contract. It does not authenticate a brokerage session, access IBKR or any other broker API, acquire real account data, or submit live/paper trades.

## Windows PowerShell

~~~powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_broker_reconcile*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli broker-reconcile --ledger examples\institutional_batch_ledger.json --snapshot examples\broker_export_synthetic.json --output local_data\broker_reconciliation.json
~~~

Both example files are **entirely fictional**. Successful matching returns MATCHED_UNVERIFIED_EXPORT, which means two synthetic JSON files agree and **does not mean either originated from a broker or grants order permissions**. No borrowing authority is created or verified.

## Required independent comparison

Input must identify the same **single** account ID, account type and currency as the local ledger. Every equity, Treasury and listed-option position must match by exact instrument type and identifier. There is no aliasing of ticker, CUSIP or OCC contract identities; ambiguous mappings fail closed.

| Financial field | Comparison |
|---|---|
| Settled cash, unsettled receivable/payable | Absolute USD tolerance (default $0.01) |
| Margin principal and accrued financing interest | Absolute USD tolerance |
| Reported net liquidation | Compare broker-export NLV with locally calculated ledger NLV |
| Imported statement's internal balance sheet | Independently verify imported cash + position values + receivables - payables - debt - interest = imported NLV |
| Equity/ETF quantity | Number of shares, not cash value alone |
| Treasury quantity | Face value, plus marked dirty value; collateral credit separately |
| Option quantity | Signed contracts, multiplier, strike, underlying, put/call right and expiration |
| Instrument mark | Compare signed market values with configured USD tolerance |
| Security universe | Block omitted positions, added unknown positions and duplicates |
| Imported collateral credit | Compare Treasury broker-recognized report-only credit; never count as NLV or cash |
| Source freshness | ISO-8601 timestamp relative to ledger's valuation time; use the stricter of snapshot and ledger age limits |
| Imported available funds and excess liquidity | Report-only; negative values block, and no reallocation/financing authority is inferred |

The results include all field differences and missing/extra position IDs, a net liquidation arithmetic reconciliation gap, and flags for source authentication and margin preview verification. Those flags stay false.

## Implementation boundary

At this phase the export carries source="imported_broker_export" as an **untrusted string**. It is *not* an authenticated proof of origin. Actual integration will need an authenticated read-only IBKR adapter, stable security identifiers (IBKR contract IDs, CUSIP, OCC symbols), source timestamps, settlement and corporate action mapping, correct broker-specific net-liquidation definitions and permissions, position pricing conventions, a secure secrets store and error/retry safeguards.

A future live adapter must never treat matching snapshots as sufficient to execute orders. Existing deterministic risk gates still need genuine broker What-If/preview data, current permissions, buying power and intraday maintenance checks, followed by operator approval and a separately gated paper-only OMS.

Do not commit actual account numbers, broker access tokens, client positions, personal data or licensed market data to a public GitHub repository. Place private exports only in the Git-ignored local_data folder.
