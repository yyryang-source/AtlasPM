# Unified API + AI ingress — P0 development batch

Branch feature/unified-api-gateway is stacked on frozen RC 5b331ea (PR #94).
No changes to main, the frozen branch, existing data schema, trading lifecycle,
model training authorization, risk policy or broker submission. No secrets,
actual account data, provider payloads or weights committed.

## Implemented contracts

- One HTTPS JSON gateway with fixed provider destinations, TLS verification,
  no redirects, environment credentials, process-local per-provider locking,
  minimum request intervals, bounded response size, public-only opt-in LRU
  cache, bounded concurrent batches (32 requests, 3 at a time, 32 MiB retained
  by default), batch deduplication, GET retry/backoff, Retry-After and cooldown.
  Default max response 32 MiB; cache 8 MiB/128 entries. JSON decoding and
  transient worker allocations add to this budget. No global hard RSS cap.
  FRED v1 requires a query credential; only injected internally, not included
  in public URL/hash/diagnostics. All other implemented credentials use headers.
- Existing Treasury/Massive CSV routines call the gateway through their
  original get_json function. Existing pagination, CSV storage and SQLite
  import remain canonical; their current page cache is not newly certified.
- SEC companyfacts fetch validates CIK/schema, writes gzip consumed directly
  by sec_fundamental_history.import_sec_factors and a local hash receipt.
  Reuse requires matching content. Explicit refresh replaces files; single
  writer only; interruption between two atomic replacements fails closed on
  the next reuse. No history-publication claim or independent authenticity.
- FRED date-vintage observations validate a bounded single page and require
  strictly earlier observation dates. Missing "." is preserved, not imputed.
  No new macro table; ephemeral exploratory interface only.
- External research adapters use configured model IDs for OpenAI Responses,
  Claude Messages and Gemini generateContent. One explicit approved request,
  fixed destinations, no tools, 64 KiB request, 2 MiB response, 128..2048
  output tokens, max 8 calls/provider/process, no automatic paid POST retry
  or fallback. Environment plus caller consent; each supplied public excerpt
  must be explicitly reviewed for external processing. No secrets/private
  portfolio/restricted data allowed. Provider retention rules still apply;
  store=false is not a zero-retention guarantee. Model outputs must contain
  verbatim evidence quotes and a descriptive interpretation. A valid quote
  does not prove the interpretation or stop all hallucinations; no calibrated
  probabilities or actionable orders. No real training, backtest or live
  permission; model version and actual reported token counts captured.
  Costs remain null unless separately verified billing/rates are available.
- Local numeric predictions route through existing content-addressed artifact
  store and predict_snapshot; no parallel model registry or duplicate fitting.
  Inventory reports installed package versions and key presence, NOT live
  availability. Local LLM server, fusion, scheduler and full research agent
  orchestration are subsequent batches. All 13 topics use one evidence contract,
  not thirteen placeholder engines.

## Provider selection and public documentation review (2026-11-24)

| Source | Role / public offering | Decision |
| --- | --- | --- |
| [Treasury Fiscal Data](https://fiscaldata.treasury.gov/api-documentation/) | Free official auction/fiscal series; not executable secondary-market quotes | Existing downloader, shared transport |
| [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | No API key; submissions/XBRL; fair access max 10 req/s aggregate/user | Companyfacts; default 2 req/s/process; contact required |
| [FRED/ALFRED](https://fred.stlouisfed.org/docs/api/fred/series_observations.html) | Key required; date-vintage query; depth/rights series specific | Bounded vintage candidate adapter; no extra warehouse |
| [IBKR](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/) | Account, positions, order and market data; entitlements often required | Retain isolated existing broker transport; verify local Paper login |
| [Massive options](https://massive.com/options) | Basic $0, 5/min, 2y; Starter $29/mo, delayed 2y; Developer $79/mo, delayed 4y; Advanced $199/mo, realtime and 5+y, individual terms | Reuse existing option CSV imports, conservative 5/min; no subscription purchase |
| [HF minute archive](https://huggingface.co/datasets/mito0o852/OHLCV-1m) | Advertised 1992–2026 Finnhub-derived files | Existing partition reader only; provenance/rights unresolved |
| [Finnhub terms](https://finnhub.io/terms-of-service) | Market/fundamental feeds; sharing, retention and business restrictions | No duplicate purchase; written rights review |
| [FMP](https://site.financialmodelingprep.com/pricing-plans) | Basic $0/250 calls/day; paid historical, news, fundamental and analyst coverage | Evaluate overlap with SEC and Massive; display/redistribution agreement required |
| [Alpha Vantage](https://www.alphavantage.co/support/api-key/) | Standard free 25/day, premium data/rates differ | Reference sample only, not full-market realtime |
| [Benzinga](https://www.benzinga.com/apis/data) | News, earnings, ratings; separate API terms | Evaluate only if no existing licensed feed fills gap |
| [Unusual Whales](https://unusualwhales.com/pricing?product=api) | Paid options flow, Greeks and dark pool; separate commercial rights | Compare with Massive options plan before purchasing |

Pricing is a public snapshot, not a quote or confirmation of user entitlement.
No keys, subscriptions or paid calls acquired. Actual historical depth,
permissions, revision semantics and rate limits depend on account/series.
No cross-provider fallback for incompatible adjustments/vintages. Existing
Yahoo/HF bulk loaders, IBKR and SEC evidence ingestion are not yet all migrated.
Disk persistence remains existing storage only. Gateway counters are process-local;
global scheduling, cross-process locks, provider-wide accounting, cost budgets,
streaming/WebSocket quotes and automated health alerts remain pending.

AI references: [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create),
[Claude Messages](https://platform.claude.com/docs/en/api/messages/create),
[Gemini generateContent](https://ai.google.dev/api/generate-content).
No model IDs hardcoded; exact model/version capabilities and pricing require
actual local key-enabled smoke. External AI prompts are not a security sandbox.

## Acceptance (Windows)

Use a separate review checkout or switch the existing review checkout only,
leaving the main data/download directory and frozen RC unchanged.
The existing .venv and dependencies are reused. All tests run offline.

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM-data-review
git fetch origin
git switch --detach origin/feature/unified-api-gateway
$py = "C:\Users\Kevin\Desktop\AtlasPM\.venv\Scripts\python.exe"
$env:PYTHONPATH = Join-Path (Get-Location) "src"
Remove-Item Env:ATLASPM_ALLOW_PAPER_ORDERS -ErrorAction SilentlyContinue
Remove-Item Env:ATLASPM_ALLOW_EXTERNAL_AI -ErrorAction SilentlyContinue
& $py -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency check failed" }
& $py scripts/maintenance/verify_release.py --minimum-passed 1024
if ($LASTEXITCODE -ne 0) { throw "Software validation failed" }
& $py -m atlaspm.data.api_gateway
& $py -m atlaspm.research.intelligence --list
```

Optional live read-only smoke only when authorized: from the existing data
root using review src on PYTHONPATH. One public Treasury call:
`python -m atlaspm.data.api_gateway --smoke-treasury`. SEC contact must be set
locally in ATLASPM_SEC_USER_AGENT (organization + contact email);
`python -m atlaspm.data.sec_download --cik 320193` writes local gzip/receipt.
FRED requires FRED_API_KEY; Massive uses MASSIVE_API_KEY and existing market-db
download commands. External AI needs provider API key, model ID,
ATLASPM_ALLOW_EXTERNAL_AI=1 and --allow-external on an explicitly reviewed
local evidence JSON file. Do not paste API keys in chat or commit them.
No brokerage call in these commands. No claim of successful live connection,
production data certification, or performance until actual results exist.

## Integration order

Keep #94 frozen until independent data-rights, actual archive quality,
PIT and performance gates are satisfied. Review this stacked feature branch
against #94, not main, and retarget only after #94 is safely merged.
Next: verified live read-only provider smoke, remaining data adapters and
standardized evidence into research, then decision/plan/risk contracts and
fixed-input human-approved IBKR Paper round trip. Do not enable automated
orders or real-data training merely because fixture tests pass.


## Designated provider decision — 2026-11-24

This section supersedes the earlier provider comparison: Yahoo Finance issuer
news; Bloomberg global/deep news and macro events; Unusual Whales options
flow; IBKR primary market data and sole execution broker. SEC, FRED, Treasury
and existing HF/local factors remain. Massive only optional historical
options reference/aggregates; snapshot realtime route rejected. No new
vendor purchases or duplicate archive downloads. No changes to frozen #94.

Public documentation / entitlement blockers:
- Yahoo [Terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html)
  restrict automated acquisition without permission. No approved Yahoo Finance
  news developer endpoint identified for this account. Obtain written feed
  access, schema/authentication, retention and AI processing permissions.
- Bloomberg [Event-Driven Feeds](https://professional.bloomberg.com/products/data/enterprise-catalog/event-driven-feeds/)
  offers licensed machine-readable news and analytics. Obtain the actual
  licensed delivery mechanism/schema and relevant rights; no web scraping,
  guessed API endpoint or paywall bypass. Terminal access alone does not
  prove these rights.
- [Unusual Whales](https://unusualwhales.com/api_lander) documents flow alerts,
  dark pool, Greek exposure, net premium, options contracts and IV rank.
  Public monthly offers: Options Starter $99 (25,000 requests/billing month,
  1y lookback), Basic $150 (2y), Advanced $375 (2y + additional endpoints);
  account/endpoint entitlement and rate limits require confirmation.
  [Terms on API page](https://unusualwhales.com/public-api) restrict
  personal-use redistribution. No external model egress from this adapter.
- [IBKR Web API](https://www.interactivebrokers.com/docs/web-api/trading/market-data/top-of-book-snapshots)
  L1 snapshots require authenticated brokerage session, accounts initialization,
  derivative secdef preflight and data subscriptions. Initial snapshot may be
  empty. [Market data lines](https://www.interactivebrokers.com/docs/general/market-data-subscriptions/market-data-lines/introduction)
  are shared across consumers; default 100 is not a guarantee of available lines.
  [TWS tick-by-tick](https://www.interactivebrokers.com/docs/tws-api/doc/md/live-ticks)
  has a 5% line limit, [depth](https://www.interactivebrokers.com/docs/tws-api/doc/md/market-depth)
  has distinct permissions/limits. Use TWS API through TWS or IB Gateway for
  true tick-by-tick/depth in a later read-only extension, not a new execution
  implementation. Do not assume simultaneous Web/TWS sessions or unlimited lines.

### New runnable features

provider_policy supplies designated roles and local operator rights gates.
An attestation is NOT independent authorization. All feeds default to no
external AI egress. Existing research_public explicitly rejects recognized
restricted provider source names even when its caller sets external_use_reviewed.
It cannot detect an intentionally mislabeled source: do not submit restricted
content manually. No external model calls originate from these adapters.

options_intelligence uses the same Gateway with UW_TOKEN, a 2s process-local
minimum interval, explicit rights and 1..1000 operator-defined request cap.
GET retries count against the cap; counters reset on process restart, not
a subscription-wide quota or cost guarantee. Flow alert page <=250 rows,
provider ID dedup/conflict rejection, chronological checks, stale cutoff,
premium/bid/ask imbalance, Call/Put counts, volume/OI ratio, IV difference,
sweep/floor/multileg/opening flags and same-contract/minute alert clusters.
No aggregate alert premium sum (alerts may overlap); no bullish inference,
block/fill direction, dealer position or multi-leg reconstruction claimed.
Separate context requests for GEX/DEX, net premium, contracts, IV rank/term
structure, dark pool. GEX/DEX component fields remain signed provider values,
not normalized dealer units. Other context raw until actual schema/units
reconciliation. Historical cursor page only, no complete pagination/stream
or persistence without licensed retention and reconciliation. No external
AI or order routes.

news_intelligence imports operator-normalized *licensed* local JSON metadata,
not vendor proprietary wire format. Input fields id,title,published_at
(offset-aware),source,url (HTTPS permanent link),symbols (vendor identifiers),
sectors (vendor categories). Each source isolated; no cross-provider mixing.
Explicit rights AND retention, bounded 4 MiB/1000-row input, UTC observation-
month metadata JSONL (16 MiB/10000 rows), content revision dedup within month,
atomic replace + exclusive single writer lock. Repeated ingestion in a new
month may duplicate metadata across partitions; no global index or new DB.
History review checks local observed_at <= cutoff, uses latest visible
revision within requested month, dedups normalized article URL, classifies
headline rules, ranks heuristic importance and shows age. Sector and ticker
matches supplied by feed, not certified security mapping or impact direction.
No article body, no automatic download, no publisher identity inference.
Production direct Yahoo/Bloomberg feed connections still require contract
and actual licensed schema; no invented endpoint.

IBKR market data reuses execution.ibkr_gateway.LocalGatewayTransport with
verified loopback TLS and existing pacing. Only fixed snapshot GET, historical
GET and scoped single-conid market unsubscribe POST added. Existing What-If
and order submission gates unchanged. MarketPool requires explicit dedicated
session, confirmed available line count 1..100, <=1000 candidate conids,
operator-provided verified conids, deterministic priority selection,
subscription reservation before request, confirmed release before rotation,
1s minimum per-pool polling. Delay/frozen/missing/crossed/stale/future quotes
do not generate fresh-book candidate. IBKR row timestamp is not field-level
timestamp. Snapshot fields include bid/ask/last/sizes/volume, option Greeks
and IV percent/OI, bond yield percent. Quote midpoint/spread only when fresh.
History bounded period/bar pairs <=1000 rows, validates OHLCV and order;
raw timestamp and volume factors not guessed. No overwrite of archive.
On error freeze new allocations; operator reauth and reconcile owned stream
release, no auto session takeover or unsubscribe-all. No persistence across
process restarts; operator must resolve orphan streams before restart. No
tick-by-tick, depth, scanner, complete option chain discovery, automatic
contract mapping, live subscriptions, real broker validation or autonomous
orders claimed. Existing account/risk/Paper paths remain unchanged.

### Local configuration checklist (never commit credentials)

| Config | Needed for |
| --- | --- |
| ATLASPM_YAHOO_NEWS_RIGHTS + _RETENTION=1 | Licensed local normalized news import; only after authorized retention |
| ATLASPM_BLOOMBERG_NEWS_RIGHTS + _RETENTION=1 | Licensed Bloomberg local export; contract/wire specs pending |
| UW_TOKEN, ATLASPM_UW_RIGHTS, ATLASPM_UW_MAX_REQUESTS | UW subscription + local approved request budget |
| ATLASPM_IBKR_MARKET_DATA_RIGHTS | Confirm API data entitlement, local contract reference |
| Client Portal Gateway browser login + trusted local CA | Authenticated dedicated read-only session, available line count and verified conids |
| ATLASPM_SEC_USER_AGENT, FRED_API_KEY | Existing official sources |
| MASSIVE_API_KEY | Optional historical only |
| OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY | Optional external research on separately reviewed public evidence |

Rights values are nonsecret contract references, not a way to grant rights.
Set actual values only after review. No API secret in git, logs or chat.
External AI is disabled during acceptance. No paid requests by tests.

### Single Windows software acceptance

Use existing review checkout and virtualenv; no downloads of financial data.
Keep main and #94 unchanged. Run latest feature head only after CI.
```powershell
& {
    $ErrorActionPreference = "Stop"
    $root = "C:\Users\Kevin\Desktop\AtlasPM"
    $review = "C:\Users\Kevin\Desktop\AtlasPM-data-review"
    $py = Join-Path $root ".venv\Scripts\python.exe"
    Set-Location $review
    git fetch origin
    if ($LASTEXITCODE -ne 0) { throw "Fetch failed" }
    git switch --detach origin/feature/unified-api-gateway
    if ($LASTEXITCODE -ne 0) { throw "Switch failed" }
    $env:PYTHONPATH = Join-Path $review "src"
    Remove-Item Env:ATLASPM_ALLOW_PAPER_ORDERS -ErrorAction SilentlyContinue
    Remove-Item Env:ATLASPM_ALLOW_EXTERNAL_AI -ErrorAction SilentlyContinue
    git log -1 --oneline
    & $py -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Dependencies failed" }
    & $py scripts/maintenance/verify_release.py --minimum-passed 1054
    if ($LASTEXITCODE -ne 0) { throw "Software validation failed" }
    & $py -m atlaspm.data.api_gateway
    if ($LASTEXITCODE -ne 0) { throw "Provider inventory failed" }
    & $py -m atlaspm.research.intelligence --list
    if ($LASTEXITCODE -ne 0) { throw "Model inventory failed" }
    Write-Host "DESIGNATED PROVIDER SOFTWARE VALIDATION PASSED"
}
```
1054 is expected baseline 1039 + 15 new test methods, not a measured result.
Optional authorized read-only UW smoke: python -m
atlaspm.research.options_intelligence --ticker SPY --limit 5.
IBKR snapshot smoke: python -m atlaspm.execution.ibkr_market_data
--conid <verified-conid> --available-lines <confirmed-free-lines>
--dedicated-session --ca-file <trusted-ca-file> --polls 2.
No live smoke executed here. No account data, proprietary payloads or
credentials should be shared. True API connectivity, data rights,
publication time, normalized units, performance, and release approval are
separate from software tests. All formal training/backtest/live remain disabled.
