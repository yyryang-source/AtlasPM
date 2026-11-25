# IBKR Client Portal Gateway — GET-only read adapter (v0.4.7)

**Scope: local Windows account capture, NOT order execution.** This module supports raw read-only IBKR portfolio information when the user personally starts and authenticates the Client Portal Gateway. Portfolio financing debit, true Treasury collateral, option identifiers, and margin requirements are NOT yet normalized to the institutional risk gate.

## Zero-network validation first

~~~powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ibkr_gateway*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-readonly --fixture examples\ibkr_gateway_synthetic.json --account DU1234567 --output local_data\ibkr_fixture_capture.json
~~~

The account ID DU1234567 is fake. The fixture run makes no network requests.

## Optional local private read when personally authenticated

Only if you actually have an IBKR account:

1. Download the official Client Portal Gateway, which requires a local Java runtime. Follow the IBKR official lesson below.
2. Extract the gateway ZIP. In the gateway directory on Windows run:
   ~~~powershell
   .\bin\run.bat root\conf.yaml
   ~~~
   Leave the gateway running and authenticate yourself in a browser at https://localhost:5000. Never supply login details, cookies or two-factor codes to this repository or ChatGPT.
3. TLS note: The gateway's default self-signed certificate may be untrusted or expired. AtlasPM deliberately does NOT support skipping TLS verification. Configure a valid localhost certificate and trust its issuing certificate in Windows, or provide the trusted PEM certificate/issuer through the optional --ca-file option.
4. From a separate AtlasPM PowerShell window, use your own account ID, never a sample ID:
   ~~~powershell
   .\.venv\Scripts\python.exe -m atlaspm.cli ibkr-readonly --allow-local-gateway --account YOUR_IBKR_ACCOUNT_ID --url https://localhost:5000/v1/api --ca-file C:\path\to\your\trusted-local-ca.pem --output local_data\ibkr_private_raw.json
   ~~~
   If the gateway already uses a certificate trusted by Python, you may omit --ca-file.

The command fails closed if the gateway is not authenticated, the account is not explicitly selected from the portfolio account list, the TLS certificate cannot be verified, or the returned account data is malformed or unsupported. No insecure-SSL flag exists.

## GET-only endpoints

- /iserver/auth/status — connected and authenticated session check
- /portfolio/accounts — obtain and select an account, before other portfolio endpoints
- /portfolio/{accountId}/ledger — raw USD account ledger including settledcash and netliquidationvalue
- /portfolio2/{accountId}/positions — recent account positions identified by IBKR contract IDs

References:
- https://ibkrcampus.com/docs/web-api/trading/portfolio-and-positions/querying-your-accounts
- https://ibkrcampus.com/docs/web-api/v1/endpoints/portfolio/portfolio-ledger
- https://ibkrcampus.com/docs/web-api/v1/endpoints/portfolio/positions-new
- https://ibkrcampus.com/campus/trading-lessons/launching-and-authenticating-the-gateway/
- https://ibkrcampus.com/docs/web-api/authentication/cpgw/client-portal-gateway-faq

## Privacy, output and trust boundary

- Full raw output includes account identifiers, holdings, balances and possibly gateway session metadata. It is **PRIVATE**. Never upload the full JSON to GitHub, ChatGPT or public issues. Save only in local_data, excluded by Git.
- Console output hides the complete account ID and all portfolio dollar amounts. It displays only class counts, authenticated status and data limitations.
- This is a local user-authenticated Gateway HTTPS response, NOT proof of approved margin, regulatory borrowing eligibility, a security haircut, option trading permission or a broker order preview.
- Margin borrowing, unsettled receivables, bond dirty price, and options contracts require a careful separate mapping to AtlasPM's accounting schema. Do not blindly map signed IBKR cash to net margin debt.
- Only a single USD-base account with identifiable USD position currency is supported at first. No FX translation or cross-account collateral use is assumed.
- No real or broker-paper orders are submitted. IBKR's local browser login may require daily reauthentication. No automatic login or secrets are stored.
- The source is restricted to GET requests to loopback HTTPS, with no redirects and no environment proxies; it uses standard TLS validation and response time/size limits.

## Remaining work

Normalize conid vs ticker/CUSIP/OCC identities, verify actual brokerage margin inputs, handle multi-currency/settlement differences, reconcile transactions and account financing, add safe transient data snapshots and fully authenticated What-If checks. The existing Paper OMS remains offline and the live trading code path does not exist.


## Paper-only local synchronization (DUR demo accounts)

After a successful local TLS read, save a local-only account selector and public
CA path once. No password, session token or private key is stored. The profile
does not grant trading permission.

~~~powershell
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-paper-sync --configure --account YOUR_PAPER_ID --ca-file C:\path\to\your\trusted-local-ca.pem
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-paper-sync --allow-local-gateway
~~~

For a bounded foreground session, add `--cycles 10 --interval 65`. The worker
checks authenticated, connected, established and noncompeting status, validates
SSO login type 2 (both documented key spellings), checks exact DEMO account
identity and USD before reading ledger/positions, and pings the documented
POST /tickle endpoint without storing its session token. The 65–120 second
interval avoids exceeding the SSO validation rate limit. Do not run competing
workers against the same session. Ctrl+C stops the worker; after a failure
re-authenticate manually and rerun. No automatic login, takeover or orders.

Use `--margin` to additionally validate the existing margin monitor in the same
capture. A margin report does not certify debt, collateral or order authority.
The latest snapshot and status are stored atomically under ignored local_data/.
A blocked or stopped status marks the snapshot non-current; a crash can leave
a stale status, so consumers must also check the timestamp and worker state.
These files are diagnostics only and are not admitted into automatic trading.

`DUR` is accepted by this read-only sync after independent SSO and account checks.
The separate Paper order guard still accepts only the existing DU numeric format;
DUR submission, What-If actual behavior and brokerage recovery need separate
local verification. Live submission, formal training and formal backtesting
remain disabled.

Official session reference:
https://www.interactivebrokers.com/docs/web-api/v1/endpoints/session/validate-sso
https://www.interactivebrokers.com/docs/web-api/v1/endpoints/session/ping-the-server


Continuous sync prints redacted progress to stderr (stdout remains the final
JSON report). The worker persists a private SSO attempt marker and waits at
least 65 seconds between its own attempts, including separate invocations.
This cannot account for other clients calling the same SSO endpoint; use one
worker and respect IBKR's one-request-per-minute limit. HTTP failures record a
sanitized endpoint label and stop without reauthentication or retry. Daily
manual Gateway authentication remains required.


### USD-only margin monitoring with auxiliary currency rows

The margin monitor reports rather than rejects a ledger merely because an
additional currency row exists. USD cash remains USD cash; with foreign rows,
the account NLV comparison uses BASE only after reconciling the sum of the
currency NLVs translated into USD (amount times reported currency-relative-to-base
rate). BASE is an aggregate and is never added to the currency sum. The margin
summary must independently agree with this BASE value. BASE cash must still
agree with USD cash under the USD-only policy. A complete, identity-matched
foreign row with zero exposure and zero NLV is compatible when totals reconcile.
Nonzero foreign cash, positions, P&L, unknown/missing fields, malformed values,
and foreign NLV without an economic explanation produce BLOCKED_DATA even when
the arithmetic reconciles. No new buying power, FX order support or trading
authorization is inferred. The account NLV reconciliation state is reported
separately from foreign exposure risk. No reciprocal rate fitting.
The sync console exposes redacted margin block reasons and currency diagnostics.
Independent financing reconciliation and the institutional gate remain required.
Provider reference: https://www.interactivebrokers.com/docs/web-api/v1/endpoints/portfolio/portfolio-ledger


### Bounded USD equity market-data probe

The existing market pool can now resolve a ticker using /trsrv/stocks and
verify the unique US STK contract through /iserver/contract/{conid}/info
(including USD currency, symbol and conid). This is broker identity evidence,
not historical security-master admission. Only use the saved Paper profile
with an authenticated dedicated session and confirmed available data lines.
Stop other AtlasPM sync/market workers first. Confirm actual feed access and
usage rights with IBKR before setting the rights reference. No automatic
subscriptions or purchase of regulatory snapshots.

~~~powershell
# Only after verifying entitlement and rights, enter your own nonsecret reference.
$env:ATLASPM_IBKR_MARKET_DATA_RIGHTS = Read-Host "Confirmed market data rights reference"
.\.venv\Scripts\python.exe -m atlaspm.execution.ibkr_market_data --symbol AAPL --symbol QQQ --available-lines 2 --dedicated-session --polls 3 --history
~~~

First snapshot initiates the feed. Subsequent polls classify real-time,
delayed, frozen and missing quotes without interpreting delayed data as live.
Optional history is a bounded 1d/5min raw QC probe. Output is redacted
metadata and counts; use --save-local only after separately confirming
retention rights and setting ATLASPM_IBKR_MARKET_DATA_RIGHTS_RETENTION=1.
The saved research-only file is local_data/ibkr_paper_market_probe.json.
Neither data nor identities are admitted to training, formal backtests or
orders. The reservation marker must remain on uncertain release or crash;
review and reconcile the owned subscriptions before manually clearing it.
This does not modify the existing DU-only order guard or the CNH risk block.
See https://ibkrcampus.com/docs/web-api/trading/instrument-discovery/contract-i-ds
and https://ibkrcampus.com/docs/web-api/v1/endpoints/contract/contract-information-by-contract-id .


### Intraday research contract and fusion integration

The existing symbol probe optionally produces descriptive technical research
using observed, contiguous 5-minute close candidates. Supply a reviewed
subtype for each symbol; IBKR STK alone cannot distinguish stocks and ETFs.
No daily factor inference or annualization from a short intraday sample.
The last returned bar is always discarded (IBKR may return partial bars);
gaps reset the trailing sequence; at least 10 contiguous bars are required.
Provider symbol, bar interval, regular-hours setting, receipt and identity
must match. The Research Contract records original availability as unknown;
Signal Fusion receives objective evidence, never an expected return.
Delayed quotes and unverified field timestamps cannot become portfolio marks
or execution prices. No volume event is generated while units are unverified.

~~~powershell
.\.venv\Scripts\python.exe -m atlaspm.execution.ibkr_market_data --symbol AAPL --symbol QQQ --available-lines 2 --dedicated-session --polls 3 --history --research-asset AAPL=stock --research-asset QQQ=etf
~~~

Without --save-local only safe research status/counts/IDs are printed.
With explicitly confirmed retention rights, the same existing probe file
includes descriptive metrics, contract and fusion receipts. No new database,
event queue writes, model admission, trading permissions or external AI calls.
Provider historical response reference:
https://www.interactivebrokers.com/docs/web-api/api-reference/trading/trading-market-data/get-md-history


### Bounded autonomous market research (foreground)

The existing Orchestrator has an explicit network-enabled research action
`market-research`. The ordinary offline `run` and `tick` remain offline.
Create a reviewed plan in ignored local_data, for example:

~~~json
{"assets":{"AAPL":"stock","NVDA":"stock","MSFT":"stock","SPY":"etf","QQQ":"etf"},
 "available_lines":5,"selection":"reviewed_rotation","move_threshold":0.02,
 "enqueue_events":false}
~~~

~~~powershell
.\.venv\Scripts\python.exe -m atlaspm.framework.orchestrator market-research --file local_data/ibkr_market_plan.json --cycles 3 --interval 65
~~~

This is an explicitly bounded foreground worker, max 20 cycles. Only during
NYSE regular hours, it rotates a reviewed universe (max eight symbols) through
the existing line budget and Paper probe. For `local_daily_rank`, reuse the
existing exploratory daily technical screener, cached per local market day;
missing or insufficient local data blocks selection, without silent fallback.
The worker does not yet discover the entire US stock universe.
An in-memory cache skips repeated successful observations with identical closed
bar content and metadata. No cached quote becomes a current mark; each cycle
still retrieves bounded history and releases owned market subscriptions.
Price-move events describe observed window return above a configured absolute
threshold, not statistical anomalies or forecasts. They are delayed/unknown
delivery with unknown original availability and no directional signal.
Queue persistence is off by default; opt in via enqueue_events=true only with
confirmed separate IBKR retention rights. Uses existing enqueue/tick, dedup,
capacity and operator controls. Actual instrument admission is still required
for real event specialist dispatch. No automatic model training, backtest,
portfolio marks, order execution or external AI. Operator stop/takeover is
checked before and after each probe; Ctrl+C ends the foreground worker.


### Actual Paper portfolio decision review (V4/V5 integration)

Run the existing foreground research command with `--portfolio-review`.
This explicitly adds one read-only account summary request per cycle using
the already authenticated Paper session. Account and research are processed
in memory; no additional raw account snapshot or licensed archive is written.
Capture age is bounded to 120 seconds, the existing margin normalizer is
reused, and the same conid associates held positions with research.
All held positions remain in scope, even outside the current research batch.
Broker-reported marked values and signed cash are diagnostic only; missing
values make aggregate exposure unknown, not zero. The USD cash-plus-position
bridge is an unexplained residual, never inferred financing principal.
The existing autonomous decision engine consumes the objective contracts.
No eligible numeric forecast means NO_TRADE; no target weights or orders
are created and the synthetic optimizer is not invoked. Skipped duplicate
research is explicitly NO_CURRENT_CONTRACT, not an exit signal.
Only redacted review status/counts/blocks are returned to stdout.

~~~powershell
.\.venv\Scripts\python.exe -m atlaspm.framework.orchestrator market-research --file local_data/ibkr_market_plan.json --cycles 1 --interval 65 --portfolio-review
~~~

CNH, model admission, field-level quote freshness, historical identity,
financing/collateral and order risk gates remain independent. This is a real
account decision diagnostics bridge, not real optimization or execution.


### Saved Paper profile risk/What-If diagnostics

Use the existing saved Paper profile and read-only risk check:

~~~powershell
.\.venv\Scripts\python.exe -m atlaspm.execution.ibkr_whatif --paper-profile-check
~~~

This revalidates the Paper session (DU or DUR), explicit DEMO USD account,
TLS, selected account, account margin fields and complete live-order snapshot.
No automatic borrowing or inference of financing principal from NLV/cash.
An active order or unresolved margin integrity issue blocks a hypothetical
order preview. Check-only does not subscribe to quotes or POST What-If.

The optional diagnostic preview requires a manually supplied symbol and
cent-aligned limit price, explicit --allow-paper-preview, existing usage
rights, dedicated session ownership (stop other market/sync workers), regular
hours, current reported real-time bid/ask, and a clean account preflight.
It is limited to one hypothetical BUY of at most 10 whole shares and $2500.
It rechecks selected account, working orders and heartbeat before the exact
What-If POST. Compare returned current margins against the account baseline
and commission-inclusive cash bound. No order placement/reply/cancel routes
are used. No automatic preview retry. The old cash-only reviewed workbench
and DU-only submission guard are unchanged. A successful diagnostic does
not prove financing/collateral, field-level quote timestamps or independent
risk approval. No preview receipt is accepted by the order journal.


Order-list preflight uses the documented force-refresh GET, discards its
initial response, and then requests an unfiltered complete snapshot using
the transport's existing per-endpoint pacing. Only snapshot=true with a
valid bounded order array is accepted. Incomplete responses are blocked,
not treated as zero orders. Stage-specific failures report safe codes and
schema flags/counts, never raw account or order data. No account switching.
Reference: https://www.interactivebrokers.com/docs/web-api/v1/endpoints/order-monitoring/live-orders


### Read-only recovery review (V7 foundation)

~~~powershell
.\.venv\Scripts\python.exe -m atlaspm.execution.ibkr_whatif --paper-profile-check --recovery-review
~~~

Uses the already authenticated account and complete order snapshot. The
existing workbench SQLite journal is opened read-only, with bounded unresolved
batch/order/fill scope; an absent journal is reported explicitly, not created.
Unresolved batches trigger a read-only trades request and reuse exact broker
ticket/fill validation. Unknown submission intent, conflicting fill evidence,
missing commission and unmatched working orders are exposed as blockers.
The review neither changes order state nor submits/cancels/resends orders.
It does not certify cash reconciliation for STKNOPT or promote observed
fills to model-training data. Existing manual reconciliation retains its
cash-account contract; actual order and fill acceptance still need testing.


### Unified foreground Paper monitoring (V8/V9 foundation)

~~~powershell
.\.venv\Scripts\python.exe -m atlaspm.framework.orchestrator paper-runtime --file local_data/ibkr_market_plan.json --cycles 2 --interval 65 --dedicated-session
~~~

Explicitly confirm exclusive ownership of the local Gateway session. During
NYSE regular hours the existing market research cycle adds portfolio,
complete live-order, recovery and position reviews in the same session.
Outside regular hours it runs account-only diagnostics without subscribing
to market data. One bounded foreground process, no service or automatic
login, no new persistence of licensed prices. Operator stop/takeover is
checked before and after each cycle and during the interval. Known margin
blocks remain visible; invalid snapshots or unresolved transport failures
stop the worker. Do not delete reservation markers or retry an uncertain
release. Resume only after operator review.

The position monitor counts observed concentration (25% reported NLV),
reported price/average-cost loss (10%) and gain (20%) thresholds, solely
as illustrative review alerts. It does not infer cost or mark when missing,
does not assume option/bond units, does not infer holding age, and does
not generate exit orders. Actual reconciled outcome and approved forecast
records remain prerequisites for model drift evaluation or retraining.
