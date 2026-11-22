# AtlasPM v0.4.4 — Offline Paper OMS Event Replay

**Research-only replay of synthetic order events. No actual brokerage orders or broker-paper orders are sent.** All brokerage preview and quote data in examples are invented.

## Windows PowerShell

~~~powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli oms-replay --ledger examples\institutional_batch_ledger.json --plan examples\institutional_batch_plan.json --events examples\paper_oms_events.json --output local_data\paper_oms_report.json
~~~

The existing multi-asset basket MUST first pass the deterministic risk gate. The engine then reads a local sequence of fake broker events. Console output summarizes order states and cash; the full audit and fills are persisted in the Git-ignored local_data folder.

## Order lifecycle and controls

| Event | Transition | Accounting behavior |
|---|---|---|
| SUBMIT | NEW -> SUBMITTED | None |
| ACK | SUBMITTED -> ACCEPTED | None |
| FILL | Open -> PARTIALLY_FILLED / FILLED | Execute synthetic cash and position changes |
| CANCEL_REQUEST | Open -> CANCEL_REQUESTED | No undo; late fills can arrive |
| CANCELED | Open -> CANCELED | Retain previously filled quantity |
| REJECT | SUBMITTED / ACCEPTED -> REJECTED | No fill |
| UNKNOWN_OUTCOME | Open -> UNKNOWN | Freeze all subsequent event processing |
| SETTLE_SALE | Matching prior SELL fill, on a later date | Move net sale receivable into settled cash, without changing net equity |

Each event has a unique event ID, monotonic sequence, aware timestamp and approved leg ID. A FILL has an execution ID, quantity, price (except a debt repayment), and fee. Duplicate identical event IDs and repeated identical executions are idempotently ignored; conflicting duplicates, overfills, invalid status transitions and fills worse than the limit price are rejected.

The replayer checks the fully approved multi-asset plan at startup, including the mock exact-leg broker preview. An approval is only a paper contract test. The plan is rechecked on each invocation; if blocked, the replay cannot begin.

## Combined portfolio accounting

Stock sales create **unsettled receivables**, not spendable cash. Only explicit SETTLE_SALE events convert a matching sale fill to settled cash, once and after the execution date. No real holiday or T+1 calendar is enforced yet.

Stock and Treasury purchases draw settled cash, reserving existing unsettled payables. Any new margin borrowing is only hypothetical, only on eligible margin accounts with explicit plan opt-in. Long put premiums consume cash; short option writing is not implemented. Margin repayment reduces settled cash and principal together and cannot silently create a replacement borrowing.

Every unique journal event triggers an independent ledger check: marked equity from holdings, settled cash and receivables less liabilities must equal opening equity plus fill-versus-mark changes and fees. A discrepancy halts processing.

Treasury collateral credit is **reported only**. It never becomes a new cash balance, and acquiring more Treasury face does not generate extra collateral credit without independent broker confirmation.

## Synthetic example outcome

The example replays 13 events. A QQQ sale is partially filled (4 shares plus 6 shares); then an existing US Treasury position is enlarged, one long protective put is acquired, and $2,000 of margin debt is repaid.

| Item | Replayed value |
|---|---:|
| Marked net liquidation | $123,945 |
| Settled cash | $3,706 |
| Unsettled receivables | $3,499 |
| Margin debt | $48,000 |
| Informational broker Treasury credit | $90,000 |
| Equity/option/Treasury order states | Four FILLED |
| Live order submission | Disabled |

All dollar amounts and events are invented; no broker has confirmed these orders.

## Remaining institution-level engineering work

This is not an authenticated, durable, production order-management system. It does not connect to IBKR, submit actual paper orders, track real-time buying power, enforce real settlement calendars, validate OCC/CUSIP identifiers, persist a signed append-only journal, handle real broker order IDs, or continuously re-risk partially filled baskets. Real brokers can fill orders during cancel requests or after a disconnect, so UNKNOWN_OUTCOME must trigger broker reconciliation before any retries.

The next stages should add authenticated read-only positions/order state, broker What-If endpoints, a replayable event database, a reconciliation controller, post-fill risk checks and operator stop controls, before any live broker privilege is considered.

Never commit brokerage credentials or personal portfolio records to public GitHub.
