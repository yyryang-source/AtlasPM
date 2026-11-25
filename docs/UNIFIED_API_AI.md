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


## Financial intelligence and interface-first credential-later batch

This section extends the designated sources; no new commercial vendor selection.
Frozen #94 remains 5b331ea. Previous #95 head 6246fc3 completed all five returned
workflows (28 jobs); Windows full gate ran 1054/1054, zero skips or audit errors.
New feature tests require fresh CI and local acceptance; no real connections.

### Implemented

* XpressAPI Agency Ratings official GET issuer/issue endpoints, bounded
  50 identifiers, validated documented response groups, agency-specific
  rating types, action/watch/outlook and effective-date fields. Supports
  as-of date or bounded 367-day history request. Coverage is always marked
  incomplete; no guessed pagination, publication timestamps or ID mappings.
  Explicit comparable global long-term basis review for notch comparison,
  no issuer/issue mixing, no mapped default/withdrawn grades, no default
  probability. Action review separates withdrawal from default, does not
  predict stock/option/bond price impact. No persistent rating store yet.
* XpressAPI login and refresh using documented form POSTs; in-memory
  XpressSession expires early and shares Gateway request budget. Explicit
  use only, no retry after ambiguous failure, no token/credential receipts.
  FINRA FIP OAuth2 client credentials token issuance reuses same auth POST
  transport; data query/dataset entitlement and renewal integration pending.
* SEC submissions recent filings and official Federal Reserve press RSS
  reuse shared Gateway. SEC acceptance retained separately, not asserted as
  publication. RSS UTF-8, 1 MiB, DTD/entity rejected, fixed official routes.
  No complete archive or guaranteed real-time dissemination.
* financial_events: strict normalized metadata contract across designated
  sources, separate credit/analyst/index event categories, receipt cutoff,
  bounded batch (2000), exact revision dedup and latest visible revision,
  future effective time, expiry, importance, holdings intersection, shared
  subject related IDs (not causal). Explicit research/risk review requests,
  no actual automatic research or order actions. Reuses news/option/credit
  adapters and SEC/Fed results; licensed file input is an internal contract,
  not invented vendor wire format. No new database or persistence; source
  reports/evidence remain local. No automatic queue, webhook/WS/stream
  ingestion, long-running scheduler, or cross-process event dedup yet.
* API Management page in research_dashboard.py; configuration via local
  environment (OS vault launcher can inject values). No key UI or plaintext
  secret file. Readiness, auth mode, process counters, success rate, latest
  successful data receipt, latency, circuit status, token expiry metadata
  when supplied, explicit public Treasury smoke. Not provider quota/fee
  verification; all actual monetary cost values null, no account entitlement
  claims. Local switches ATLASPM_ENABLE_<PROVIDER>=0 (or the workbench current-process enable/disable controls) block applicable gateway
  and rights-guarded adapter calls; no order permission changes. Public
  legacy calls remain explicit, not auto-started.
* Existing external AI public excerpt guard extended to recognized ratings,
  index and investment-bank source names; all event/credit outputs deny
  egress, formal training/backtest/live. String-based guards cannot detect
  intentional source mislabeling. No automatic transmission of licensed
  data or unreviewed model predictions. Statistical fusion and event-driven
  model execution still pending.

### Consolidated procurement and configuration (no purchase required now)

Contract/retention/AI processing review before setting ATLASPM_<SOURCE>_RIGHTS.
For earlier Yahoo/Bloomberg and UW/IBKR variables use the previous checklist.
All commercial prices below are quote/entitlement dependent unless specifically
published. No fake pricing, no commitment, no vendor data copied into git.

| Provider / official entry | Required package / auth | Cost and blockers |
| --- | --- | --- |
| Yahoo Finance [terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) | Licensed news feed, actual authentication/delivery spec | Quote; no official news wire contract available; normalized authorized file only |
| Bloomberg [Event-Driven Feeds](https://professional.bloomberg.com/products/data/enterprise-catalog/event-driven-feeds/) | Licensed machine-readable news and institutional research, non-display rights | Quote; licensed wire schema and permissions pending |
| [Unusual Whales API](https://unusualwhales.com/public-api) | UW_TOKEN, authorized option flow + context endpoints; ATLASPM_UW_RIGHTS, ATLASPM_UW_MAX_REQUESTS | Previously documented Starter $99/mo; higher coverage/rates by plan; verify at purchase |
| [IBKR](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/) | Existing Gateway login/TLS and actual data entitlement; available lines; no unattended MFA bypass | Exchange/account-specific; no duplicate execution system |
| [XpressAPI Agency Ratings](https://xpressapi.marketplace.spglobal.com/swagger/) / [request information](https://pages.marketintelligence.spglobal.com/RatingsXpress-Info-Request.html) | Aggregate S&P/Moody's/Fitch issuer & issue rights; XPRESSAPI_TOKEN or XPRESSAPI_USERNAME + XPRESSAPI_PASSWORD; ATLASPM_XPRESSAPI_RIGHTS, ATLASPM_XPRESSAPI_MAX_REQUESTS | Quote; prioritize one aggregate contract, confirm each agency entitlement, no independent vendor purchase by default |
| Morningstar DBRS / KBRA | Supplement only when coverage gap proven; licensed export and schema | Quote; direct adapter blocked pending spec |
| Global investment banks | Authorized research distribution, analyst estimates and rating history; each license/firm delivery specs | Quote; no scraping/paywall bypass or invented endpoint |
| [S&P DJI](https://www.spglobal.com/spdji/en/about-us/data-index-licensing/), [MSCI](https://www.msci.com/eqb/gimi/stdindex/index_review.html), [FTSE Russell](https://solutions.lseg.com/ftse-russell-client-service) | Licensed announcement/constituent/weight feeds, effective dates and vendor IDs | Quote; normalized event intake only until wire specs |
| [SEC](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | ATLASPM_SEC_USER_AGENT; companyfacts and submissions JSON | Free public API; fair access, publication/identity validation |
| [Federal Reserve RSS](https://www.federalreserve.gov/feeds/feeds.htm) / [FRED](https://fred.stlouisfed.org/docs/api/fred/) | Public RSS; FRED_API_KEY for data; series rights review | Public RSS free; FRED series-specific conditions |
| [Treasury FiscalData](https://fiscaldata.treasury.gov/api-documentation/) | Existing public API | Free, not secondary-market executable price |
| [FINRA Developer Center](https://developer.finra.org/APICredentials) / [fixed-income terms](https://developer.finra.org/specific-terms-fixed-income-data) | FINRA_CLIENT_ID + FINRA_CLIENT_SECRET; ATLASPM_FINRA_RIGHTS + ATLASPM_FINRA_MAX_REQUESTS; OAuth2 client credentials; exact licensed TRACE dataset | Credential/dataset-specific fees; token adapter only, no bond transaction query yet |
| [MSRB data subscription](https://www.msrb.org/Market-Data-and-Research/Data-Subscription-Services-and-Products) | EMMA/RTRS applicable subscription, licensed delivery schema | Public EMMA access differs from paid realtime feed; quote/fee schedule, direct data adapter blocked |
| Local history & Massive | Existing archive; optional MASSIVE_API_KEY for historical only | No repeat download or realtime purchase; separate RC rights/PIT certification |
| OpenAI / Claude / Gemini | Existing API keys and explicit external public evidence egress opt-in | Usage-based, model-specific; no commercial feed egress by default |

No fees estimated for unavailable contracts. The actual procurement decision
must include written non-display/internal research, historical retention,
derived data and external AI/training rights. A public website or Terminal
subscription does not prove machine-readable redistribution rights.

### Concentrated offline acceptance

New tests expected count 1068 (previous confirmed 1054 + 14); this is
an expectation, not measured. Use prior Windows review checkout flow,
replace minimum-passed with 1068, leave external AI and Paper opt-in unset.
No credentials needed and no paid connection should occur in tests.
Existing workbench extra (Streamlit) needed only to open API page; use
`python -m streamlit run src/atlaspm/research_dashboard.py --server.address 127.0.0.1`
from the review checkout. Do not expose dashboard to public networks.
After legitimate credentials are configured, an explicit aggregate ratings
schema smoke:
`python -m atlaspm.research.credit_intelligence --scope issuer --identifier-type LEI --identifier <reviewed-LEI> --as-of <date>`
(or --login with username/password configured; do not include keys in CLI).
Only sanitized aggregate evidence printed; no live API test run here.
FINRA OAuth and all paid feed smoke require separate operator action.
Official documented schemas are candidates until tested against entitled
responses. Formal training, formal backtest, automated live remain disabled.


## Institutional Research Engine — licensed documents and exploratory factors

Primary delivery: [Bloomberg enterprise investment research data](https://professional.bloomberg.com/products/data/enterprise-catalog/investment-research-data/) and [Bloomberg research](https://professional.bloomberg.com/products/bloomberg-terminal/research/). Confirm full-text third-party broker reports, machine-readable entitlement, contributor-specific permissions, historical availability, document retrieval schema and local processing/retention. The terminal or a news license alone is insufficient.
Conditional alternatives only (no purchase or enabled network adapter): [FactSet Investment Research API](https://developer.factset.com/) exposes research search/retrieval; [LSEG investment research](https://www.lseg.com/en/data-analytics/asset-management-solutions/investment-research-analytics) / [Data Platform APIs](https://developers.lseg.com/en/api-catalog/refinitiv-data-platform/refinitiv-data-platform-apis) offer broker research and document retrieval products. Obtain exact licensed specifications and compare contributor coverage, archival point-in-time quality and cost before choosing any fallback. No guessed endpoints or automatic paid document purchase.
IBKR/Yahoo and bank sites are supporting evidence only; no scraping, no paywall circumvention. Registry includes the 18 named firms, not a claim that every report or analyst is covered.

### Local ingestion and evidence

Use the existing gitignored local_data/runs area, private_path and atomic write helper. No new database or document copy. Report originals must remain immutable under local_data, including prior revisions. The bounded JSONL index (16 MiB / 5000 revisions) uses exclusive writer lock and atomic replacement; a stale lock requires operator review. Hashes bind original bytes, metadata, extracted claims and receipt, checked against originals at read. Hashes are integrity checks, not signatures or independent proof of historical availability; an operator able to rewrite all local records can fabricate them. This is not a certified historical training archive.
Separate document rights from news/market licenses: ATLASPM_BLOOMBERG_RESEARCH_RIGHTS (local nonsecret reference) and ATLASPM_BLOOMBERG_RESEARCH_RIGHTS_RETENTION=1, or the corresponding INVESTMENT_BANKS / IBKR / YAHOO_FINANCE variables. No external model egress or training license inferred. Management inventory shows configuration status only. The shared external public excerpt guard rejects the 18 firms plus FactSet/LSEG; source-name mislabeling remains an application-layer limitation.

Metadata JSON example (synthetic, replace with legitimately reviewed real metadata only):
```json
{
  "provider": "bloomberg",
  "firm": "Goldman Sachs",
  "analyst_id": "fixture-analyst",
  "report_id": "fixture-report",
  "subject": "conid:123",
  "evidence_type": "original_report",
  "published_at": "2020-01-01T12:00:00Z",
  "first_available_at": "2020-01-01T13:00:00Z",
  "source_url": null,
  "document": "local_data/research/example.txt"
}
```
Only approved typed subject IDs (conid, CIK, ISIN, index, macro); mapping and analyst alias equivalence must be independently reviewed. Evidence types original_report, news_relay, rating_summary, ai_inference are distinct; only original reports with known availability enter numeric factors. Receipt assigned on local import, not backdated from source dates. Unknown first availability remains null. No raw report content or proprietary data in Git.
UTF-8 text and optional PDF using pypdf (install institutional-pdf extra separately; no new core dependency). PDF signature, encrypted/page limit, 8 MiB input, 500k extracted characters and 128 claims limit; pypdf decompression not a hard RSS limit or sandbox. Use a separately isolated process for untrusted documents in production; scanned/OCR/layout and arbitrary table extraction remain unimplemented. Actual PDF parse and resource measurements require local acceptance.
Conservative explicit tagged lines, e.g. `Rating: Buy`, `Rating Action: Upgrade`, `EPS (USD, 2027-12-31, GAAP): 5.20`, `Target Price (USD, 2027-12-31, split_adjusted): 120`, `Risks: ...`. Preserve per-page exact line/quote hash. Revenue/EBITDA, index/rate/macro forecasts and tagged valuation, industry, thesis, risks, bull/bear and allocation text supported. No interpretation of unlabeled prose or extraction of full valuation models. Units/scales must be explicitly normalized by the licensed source; no silent conversion of millions/billions, adjusted/GAAP or target horizons. Do not retype a forecast into a text file and call it the original report.

### Factors / evaluation / event integration

Compare separate as-of snapshots on the same subject, exact currency/period/basis and reviewed firm/analyst IDs. Only local receipt and stated availability before cutoff, no future revision overwrite. Rating Buy/Hold/Sell explicit only, withdrawals remove coverage instead of treating as Sell. No inferred scale from overweight/outperform. 180-day age default, 30-day illustrative decay. Paired rating momentum, target/EPS/revenue relative revision (absolute prior denominator, skip zero), target dispersion (population std/mean), consensus and disagreement, coverage count change, age-decayed rating. Sentiment is a rating proxy; event surprise is relative to other analysts' prior EPS estimates, not market surprise. Each forecast metric may require a separate call with its actual common basis/horizon. Missing values null, sample counts and receipt evidence preserved. Not independently calibrated or formally PIT certified.
Descriptive forecast error uses locally supplied exact-basis outcomes observed after horizon, one first forecast per analyst/target/horizon bucket, includes historical report revisions. Absolute and relative errors and sample counts, no formal return backtest; outcomes/digests are operator-provided and not independently certified. Historical analyst accuracy factor and dynamic weights remain null until independent out-of-sample evaluation, coverage/selection bias, benchmark/sector and trading-cost controls are implemented. No reputation weighting. Separate review_rating_outcomes accepts explicitly reviewed observed security/market/industry returns and cost bps bound to eligible rating receipts. Entry must follow local availability within three calendar days; outcome observed after exit, horizons 1..365 days, overlapping intervals excluded per analyst/horizon. Reports raw excess returns and signed net descriptive association, nonannualized mean-to-dispersion where defined, coverage and evidence IDs. Not a realized strategy return, Sharpe ratio, or formal backtest. Actual benchmark/return provenance, selection bias, stability, market pricing and independently calibrated weights remain pending.
event_review reuses financial_events for analyst ratings and earnings estimates plus caller-supplied normalized SEC/news/options/macro/market events. Explicit shared IDs only, no causal inference or trading authority. Event conversion additionally needs ATLASPM_INVESTMENT_BANKS_RIGHTS for the existing normalized event source. No automatic model call; local report outputs retain deny flags and cannot be promoted into formal training/backtesting or orders. Unified model fusion/decision/portfolio binding pending separate validation.

### Acceptance

Previous d96cd9a CI: 3/5 workflows success, full Windows 1067/1068 (1 error, no skips). API management field mismatch last_latency_seconds corrected in 6ae58a5. New offline institutional tests cover document integrity, history, provenance, factor calculations, withdrawal, forecast errors, event bridge and egress. Expected full count 1079 (1068 + 11), not measured. Run the same previous Windows review checkout validation, minimum-passed 1079; keep external AI and Paper opt-in unset. No subscription credentials needed. Use review src via PYTHONPATH from main local_data root only for authorized report import.
```powershell
& $py -m atlaspm.research.institutional_research --import-metadata local_data/research/report.json
```
CLI prints sanitized import summary; factors and evaluation callable via explicit arguments (see --help). Real documents, licensed feed access, PDF dependency, actual identity/basis/PIT, performance and AI-processing rights not validated. No real API calls made. Keep #94 frozen and all formal training/backtest/live disabled.


## Unified research, fusion, decision and synthetic lifecycle batch

Base 3363512; frozen PR #94 remains 5b331ea. Earlier latency-contract
fix 6ae58a5 independently passed its five returned workflows. No inherited
PASS for this batch.

- research_contract: bounded typed IDs and asset classes, distinct objective,
  numeric forecast, institution opinion and LLM inference; evidence hashes,
  available/receipt/generated/expiry chronology, horizon, risk/invalidation,
  freshness and rights. Unknown availability stays unknown. A hash or caller
  fixture flag is not proof of external source authenticity. Real specialist
  reports can be adapted descriptively via descriptive_report; numeric
  admission is strictly synthetic. No verified external historical mapping.
- signal_fusion: same subject/asset/horizon only, fixed explicit experimental
  policy, reject duplicate model, collapse overlapping evidence components,
  conservative disagreement and direction veto, uncertainty envelope (not
  confidence interval), no subjective sentiment-to-return mapping. Historical
  diagnostic wrapper reuses training_monitor and training_ensemble with
  bounded synthetic streams; no promoted weights or calibration. True
  correlation, analyst weights, market regime and out-of-sample real evidence
  remain pending.
- intelligence.dispatch_tasks: max eight sequential explicit public/local
  artifact tasks, validates public egress before calls, requested output-token
  budget, one result per identical task in batch, no automatic fallback.
  No persistent cache/actual billing or autonomous model selection; restricted
  feed text still cannot enter external AI. Existing model inventory/registry
  retained. No trained model used in fixed fixture.
- autonomous_decision: BUY/SELL/HOLD/NO_TRADE research, conservative edge
  after cost and uncertainty, exact horizon separation. Synthetic cash
  portfolio bridge requires complete positive held stock/ETF candidates,
  annual horizon, typed SYN IDs, current synthetic liquidity, spread/cost
  and existing independent risk constraints. Does not turn HOLD/NO_TRADE
  into a liquidation. Options/bonds remain research only. No broker calls.
- autonomous_cycle: fixed synthetic events and forecast values (not trained
  AI), existing portfolio allocation, risk, institutional preflight and
  Paper OMS, actual simulated fill/position state; fixed 5% mark shock,
  independent sell-only exit via the same order builder, preflight and OMS,
  explicit synthetic next-day settlement, flat reconciliation and P&L
  decomposition. Full fill/settled entry required; blocked/partial/rejected
  paths remain explicit. No real exchange calendar or real trades.
- event_queue: bounded atomic metadata queue in existing local_data/runs,
  max 1000 revisions / 8 MiB, per revision dedup, priority, max 100
  local review tasks per drain, retry up to 3 then quarantine, restart reads
  pending state. Retention attestation required for commercial data;
  exclusive lock, manual stale-lock recovery. No continuous polling daemon,
  automatic AI dispatch, cross-process network side effects, archive compaction
  or certified delivery yet. No new database.

Credit rating normalized history now has explicit --retain and local receipt cutoff, per-row revision dedup and source hash; shared atomic writer in local_data/runs, retention requires ATLASPM_XPRESSAPI_RIGHTS_RETENTION=1. Effective dates still not publication times. Queue marks earlier pending revisions superseded at cutoff and supports a bounded explicit CLI worker (python -m atlaspm.research.event_queue --cycles 3 --interval 5). No autonomous network scheduling.

Remaining: complete specialized report/bond data history and actual
security mapping, source-backed evidence verification, real-time stream
scheduler, automatic model routing with observed performance and cost,
validated risk/return models, cross-asset execution and verified real data.
No formal training, formal backtest or automatic live permission.

Expected tests 1090 (previous 1079 + 11), not measured. From the existing
Windows review checkout, use previous consolidated validation with
--minimum-passed 1090, then:
```powershell
& $py -m atlaspm.framework.autonomous_cycle
if ($LASTEXITCODE -ne 0) { throw "Synthetic event-to-flat validation failed" }
```
No real API or order call in fixture. CLI prints only fixture status and
attribution, not a production return. Actual full-suite CI and local
performance not yet measured.


## Autonomous orchestrator and continuous lifecycle batch

Base c4bb1c2. The existing financial event queue now holds a reserved
system-control record, atomically committed with the local review tick.
NYSE calendar via existing pandas_market_calendars, phase classification
(pre-market/market/post-market/closed), minute tick id, monotonic cutoff,
single writer, pause/takeover/resume. The local foreground worker is bounded
(100 cycles), external network, model training, order tasks remain disabled.
Stop is a local event-worker latch, not a real broker cancel or global kill
switch. A running atomic review must finish before control can acquire the
lock. Crash recovery reads the last committed state; stale exclusive locks
require operator review/removal after verifying no writer remains.

Continuous synthetic simulation accepts at most 60 chronological sessions,
20 symbols, 128 research rows/day. Each close uses existing decide/fuse and
requires full positive stock/ETF research coverage before passing candidate
signals to existing engine.replay. Missing/stale/conflicting input holds
rather than implicitly liquidating. Existing next-open fills, prior volume,
cash, settlement, trade costs and attribution are reused; no new OMS. Optional
existing rolling optimizer can be supplied. Added opt-in cash opening-gap and
target gross/stress checks, stop/take-profit/time limit from first acquisition
mark. Conservative account-wide sell-only latch retries residual positions
under original liquidity rules; not individualized dynamic stops or a broker
risk approval. Checkpoint uses existing atomic writer under local_data with
.continuous.json suffix, policy and ordered input prefix hashes. Recomputes
bounded prefix; rejects changed history and duplicate input. No real trades
or production account state. Final flat/settled flag is computed, not assumed.
No real market data, real model predictions or actual AI API call in fixtures.

Fusion supports optional experimental freshness half-life, and residual
correlations on matured aligned synthetic date blocks. Freshness checks both
source availability and local receipt; envelope not calibrated. Joint local
research accepts normalized events and research contracts, exact subject and
event evidence hash/receipt/availability checks, explicit missing specialist
coverage. No automatic specialist data retrieval or trained model inference.
Learning review masks unmatured labels, reuses existing monitor and ensemble,
reports correlation and review requirement; no training, registry promotion
or rollback action. Workbench overview reuses existing navigation and adds
local scheduler state and controls; no new dashboard. Actual API usage,
performance telemetry, continuous ingest, verified identity and real-time
research, option/bond execution, full task scheduler and model approval remain
pending. No real connection tested, no resource benchmark measured.

New tests cover continuous buy/stop/exit/settlement, partial/rejected orders,
research conflict/missing, risk budget, restart prefix, exclusive writer,
control/tick idempotency, decay/correlation, joint local context and stale
availability. Expected count 1100 (1090 + 10), not measured. Run existing
Windows release gate with --minimum-passed 1100, then:
```powershell
& $py -m atlaspm.framework.orchestrator status
& $py -m atlaspm.framework.continuous_simulation --help
& $py -m unittest discover -s tests/research -p test_continuous_orchestrator.py -v
```
No paid credentials required. Keep #94 frozen, formal training/backtest and
automatic live disabled. Do not treat earlier CI as current acceptance.

Multiasset position review reuses institutional ledger valuation with synthetic option multipliers, signed delta-dollar exposure, gamma/vega illustrations and Treasury DV01, financing-cost assumptions and explicit NO_TRADE. No option/bond sizing or verified margin/credit approval; no real execution.


## Offline Release Candidate integration batch (not yet approved)

Starting head 6e98ae2: actual Windows full optional suite 1098/1100,
one assertion failure and one error, zero skips. The prior c4bb1c2
full Windows suite was 1088/1090 with the same failures. Fix 14c112e
preserves the original assertions: revision-event means include only new
matched report receipts, not unchanged carried-forward forecasts; genuine
new reiterations count zero. Consensus retains all comparable forecasts.
The OMS-to-Holding adapter converts only finite exactly integral whole-share
values (rejects fractional, negative, boolean, nonfinite or oversized values).
No change to Holding's strict contract, account risk policy or order limits.

The unified entry is:
```powershell
python -m atlaspm.framework.orchestrator offline
```
Uses isolated synthetic event queue namespace, actual existing event
deduplication, NYSE calendar, joint evidence review, experimental fusion,
continuous replay with next-session fills, position stop, settlement and
attribution, checkpoint rerun, existing independent OMS cycle and existing
matured-label learning monitor. Synthetic events use an explicit synthetic
provider and synthetic subject, never a vendor identity. Fixture model
request/response schemas for all three providers are exercised without
credentials or transport; a separate integration test runs actual dispatch
against an injected in-memory opener, including dedup, budget and malformed
response. Neither is real inference. Current continuous path uses fixed
synthetic forecasts and existing cash replay; it is not a live OMS,
a general automatic multiasset execution engine, or trained AI performance.
Timing fields are actual process CPU/wall measurements only when executed;
production CPU/RSS/disk/throughput remain unverified. The local stop latch
is checked between stages, not a broker cancellation or mid-transaction
interrupt. No release approval returned by the fixture even on PASS.

CI now requires at least 1105 executed successful tests on the full Windows
gate, with zero skips, and runs the unified offline command on both the
Windows and Linux full dependency gates. Expected 1105 = 1100 + 5 new
test methods; do not treat as measured until the exact head passes.
Use same Windows review checkout with --minimum-passed 1105 then
python -m atlaspm.framework.orchestrator offline. No real credentials.
If checkpoint conflicts after a version/fixture change, use a new explicit
local checkpoint ending .continuous.json; do not remove old data silently.

### Consolidated local configuration and subsequent live verification

Use existing API management inventory and earlier designated provider
documentation above; this is not a purchase instruction. Keep all credentials
in a local OS-vault launcher/environment; do not put values in Git or logs.
Set nonsecret contract reference variables only after confirming rights.
Every license must separately cover acquisition, archive retention,
machine processing, third-party AI transmission and training.

| Provider | Required local preparation | Actual connection step after permission |
| --- | --- | --- |
| IBKR | Local Client Portal/TWS session, Paper account and market data entitlements; ATLASPM_IBKR_MARKET_DATA_RIGHTS | Read-only account, contract mapping, quotes, Greeks, subscription limits, What-If; human-approved Paper lifecycle only |
| Yahoo Finance | ATLASPM_YAHOO_NEWS_RIGHTS; licensed feed specification and retention rights | Authorized feed/file ingestion; no guessed URL |
| Bloomberg | ATLASPM_BLOOMBERG_NEWS_RIGHTS, ATLASPM_BLOOMBERG_RESEARCH_RIGHTS plus explicit retention rights, enterprise delivery entitlement | Licensed news and third-party research schema/receipt smoke |
| Unusual Whales | UW_TOKEN, ATLASPM_UW_RIGHTS, ATLASPM_UW_MAX_REQUESTS | One bounded flow request then context/units/coverage checks |
| Investment banks | ATLASPM_INVESTMENT_BANKS_RIGHTS and corresponding document RESEARCH_RIGHTS / RETENTION | Licensed original report import and identity/PIT validation; Bloomberg preferred |
| XpressAPI (S&P/Moody's/Fitch) | XPRESSAPI_TOKEN or approved local username/password, token expiry, ATLASPM_XPRESSAPI_RIGHTS, ATLASPM_XPRESSAPI_MAX_REQUESTS | One rating scope request, independent publication/basis/coverage review |
| S&P DJI/MSCI/FTSE Russell | Corresponding ATLASPM_*_RIGHTS and documented authorized delivery | Licensed normalized index-event import |
| SEC EDGAR | ATLASPM_SEC_USER_AGENT | Companyfacts and disclosures, fair-access rate, source/receipt audit |
| FRED/Federal Reserve | FRED_API_KEY; official public policy documents | Date vintage, publication and revision validation |
| Treasury FiscalData | Public official feed; no token | Bounded public auction check, not market quotes |
| FINRA/MSRB | FINRA_CLIENT_ID / FINRA_CLIENT_SECRET, ATLASPM_FINRA_RIGHTS / MAX_REQUESTS, MSRB authorized data delivery | Token/auth smoke only where documented, then licensed dataset schema |
| OpenAI/Claude/Gemini | OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY, configured model IDs, explicit approved public excerpts | Single bounded manual inference per provider; actual usage and rights audit |
| Existing HF/Massive/local archive | No duplicate download; MASSIVE_API_KEY only if optional historical reference authorized; archive rights review | Independent identity/action/PIT and performance audit |

Provider inventory is authoritative for actual implemented env names;
pricing, entitlement, quota, retention and API addresses must be confirmed
from supplier agreement and official documentation before paid activation.
No credentials supplied or real calls made in this batch. PR #94 remains
frozen, formal training, formal backtest and automatic live remain disabled.


## Offline V1.0 functional integration checkpoint (not approved)

Actual c0cc70c Windows full dependency suite: 1104/1105, one rating
momentum assertion failure, zero errors/skips, 95.474 s test runner.
Previous OMS integer adapter and unified fixture test had no failure.
Fix 668b4fa preserves matched-panel rating momentum (unchanged analysts
included) separately from new-receipt numeric revisions; assertions unchanged.
Basic Python CI had incorrectly invoked full offline command without optional
calendar/portfolio dependencies; restored base tests, full Windows and Linux
jobs explicitly run the offline benchmark instead.

Event queue now automatically routes normalized metadata into bounded local
descriptive research contracts for synthetic subjects. Real events without
verified instrument mapping are blocked, no numeric prediction or provider
call. Write-failure injection verifies prior committed queue state survives
and retries once. Existing mixed-asset exact-leg OMS fixture (equity sale,
Treasury purchase, protective long put, debt repayment) now persists full
validated event prefix, account and order state and audit digest under local
data. Partial fill recovery recomputes and checks prior persisted state, then
extends; no duplicate cumulative fills, no production broker recovery.
This is not general cross-asset optimization, exercise/assignment handling or
real broker settlement. Existing risk and margin restrictions unchanged.

Explicit local specialist dispatcher: `python -m atlaspm.framework.orchestrator
research --file local_data/research_tasks.json`. JSON contains as_of and
tasks, each {route, params}. Routes yahoo_finance/bloomberg month, institutional
subject/previous_as_of/unit/period/basis, credit_history identifier_type/
identifier, factor_admission months (max 3, structural audit only), local_artifact
artifact_db/artifact_id/payload, events normalized event list. Max 16 tasks;
same task dedup, local rights and existing schemas enforced. CLI emits hashes
and status, not restricted report contents. Other designated routes explicitly
blocked until verified local input or authorized integration is available.
Not a fully automatic specialist or paid AI scheduler. No independent identity
or verified model performance claim.

New offline benchmark reuses existing OS RSS/I/O functions:
```powershell
python scripts/maintenance/verify_release.py --minimum-passed 1109
python scripts/maintenance/benchmark_unified_data.py --offline
```
Expected 1109 (1105 + 4 tests), not measured. Benchmark calls canonical offline
entry and reports actual CPU/wall/lifetime RSS and process I/O when executed.
Default checkpoint under current checkout local_data; if old fixture changed,
use a separate review checkout or versioned checkpoint for direct offline CLI.
No new database, duplicate HTTP/OMS or task framework. No code deleted without
evidence. Source audit on c0cc70c showed 276 modules, 175 test files, 66 shims,
zero duplicate body candidates and zero errors. This is not final source
certification. Remaining: latest CI/Windows, automatic real and local specialist
generation across all sources, genuine model inference/routing, general
multiasset allocation and lifecycle, production durable recovery/kill switch,
independent research promotion/rollback scheduling, performance and real
data/rights verification. Keep #94 frozen; formal training/backtest/live disabled.


## Offline V1.0 final integration release gate (candidate, not approved)

Independently verified prior head 84fe21809eb3ec77e499ddae2ec6e3003751bbb7:
all seven returned GitHub workflows successful, Windows full 1109/1109
zero failures/errors/skips, source errors and duplicate candidates zero.
Windows tests 87.611 s; offline benchmark PASS, 2.1363836 s wall,
2.171875 s CPU, process lifetime peak RSS 152698880 bytes, process I/O
22441098 read bytes and 276869 written bytes. This measures a bounded
synthetic fixture, not real market performance or physical disk transfer.
Evidence: https://github.com/yyryang-source/AtlasPM/actions/runs/45510372198/job/138856258246
Linux full dependency gate also passed:
https://github.com/yyryang-source/AtlasPM/actions/runs/45510372066/job/138856257608

Final batch adds all-category synthetic event metadata contracts and a
deterministic partition of topics across three fixed model response schemas.
Joint local evidence checks and existing decision/fusion run on the combined
context plus separately fixed synthetic numerical forecasts. No real provider
text retrieval, trained prediction or external inference claimed; no synthetic
LLM output becomes a numerical signal. Actual specialist archive calls remain
bounded explicit local routes; unavailable vendor schemas remain blocked.

Existing mixed basket OMS now exercises partial fill, cancel pending,
confirmed cancellation and unknown outcome with immutable checkpoint and
cash/position reconciliation; unknown outcome remains halted. A process-exit
test terminates the actual writer after committed replacement before lock
cleanup; explicit operator cleanup after confirming the process has exited,
then replay produces zero duplicate completions. This is not power-loss
durability or arbitrary broker recovery. No real kill switch or automatic
stale lock stealing.

Experimental rank_synthetic_baskets compares at most eight already specified
exact-leg synthetic baskets through the existing independent preflight and
instrument-specific lower return bounds (option premium, dirty bond value,
equity); rejects missing evidence, short options and maturity crossing
the forecast window; deducts assumed financing, entry fee and configurable
conservative stress penalty. No automatic order execution, no calibrated
return/covariance optimization, no general option assignment or bond sale
settlement. Existing risk limits preserved; no fictitious broker preview.

Existing learning review now emits review_schedule drift requests; model-review
CLI reads and compares content-addressed alpha registry versions, refuses
same version, marks different dataset/config incomparable, no promotion or
rollback activation. Independent validation required for any new version.
Workbench status includes counts of persisted specialist topics and blocked
tasks. No new database/OMS/HTTP client/model framework; no code removed
without evidence of redundancy.

Latest expected full Windows count 1112 (1109 + 3 test methods), not a
measured result. Full Windows gate and Linux full-dependency job run
benchmark --offline; benchmark reports actual runtime/CPU/RSS/I/O and
bounded workload counts, not a sustained throughput claim. Use:
```powershell
python scripts/maintenance/verify_release.py --minimum-passed 1112
python scripts/maintenance/benchmark_unified_data.py --offline
```
Model comparison: `python -m atlaspm.framework.orchestrator model-review
--file local_data/model_review.json`, with exact keys path (existing local
registry .sqlite3), champion, challenger (registered strategy versions).
No promotion action. Credential list and authorized staged real tests above
remain the canonical preparation instructions; provider contract, limits,
fees and external-AI rights must be independently confirmed before activation.
After latest full gates, freeze feature expansion and fix observed defects only.
No release tag/merge/approval until exact SHA and Windows user gate pass.
Real data/PIT/rights, actual AI and IBKR Paper connections, production recovery,
generalized cross-asset optimization/expiry and independent operational model
approval remain explicit blockers. #94 unchanged. Formal training, formal
backtest and automatic live remain disabled; real Paper manual only.


## Final bounded completion batch (after verified e46d83c)

Independently checked e46d83c430379b06266d6f5d6265a56318b356f6:
all 7 workflows passed; Windows full 1112/1112, no failures/errors/skips,
zero source errors or duplicate body candidates, tests 63.075 s.
Offline PASS: 1.5849976 s wall, 1.5625 s CPU, 152707072 B peak RSS,
22585765 B read / 288339 B written process I/O. Linux full 1112 tests
39.879 s; offline PASS 1.670723918 s wall, 1.670130473 s CPU,
220344320 B peak RSS, 0 read / 339968 B written OS IO counters.
Both fixtures: 16 queued events, 20 joint events, 5 replay sessions,
13 mixed journal events. Windows 10.09465 queued events per total wall
second, Linux 9.57669. Not production throughput or physical disk traffic.
Windows evidence https://github.com/yyryang-source/AtlasPM/actions/runs/45512442516/job/138863313061
Linux evidence https://github.com/yyryang-source/AtlasPM/actions/runs/45512442401/job/138863313285

New local routes use existing unified reader: technical_local (existing
stock screener), fundamental_local (existing provenance-bearing reader),
sec_local, bond_local, factor_local (descriptive evidence hashes).
Require prior NY date, <=367 days, <=8 symbols, <=4096 selected rows.
Fundamental file <=64MiB. Technical source mixing rejected. These are
exploratory source receipts, not verified permanent identity, PIT,
numerical forecast, or licensed external-model evidence.
No new DB or raw commercial data retained by dispatcher.

Optional operator-defined event-to-local-research bindings:
```json
[{"kind":"volume_anomaly","subject":"SYN:AAA","task":{"route":"technical_local","params":{"start":"2019-01-10","end":"2020-01-09","symbols":["AAA"]}}}]
```
Save under local_data, then run:
```powershell
python -m atlaspm.framework.orchestrator run --file local_data/research_bindings.json --cycles 10 --interval 60
```
Example dates/IDs only; actual bound task params must match local data
and desired decision cutoff. Max 16 bindings, 4 per event category and
4 local calls/tick; no automatic identity mapping, no paid access.
Only report hash/status are committed into existing event queue.
Unavailable input produces explicit blocked receipt; completed metadata
is not evidence of completed specialist analysis. Fixed binding ranges
need operator maintenance. Real data licensing and identity remain
uncertified. Local artifact route rejects future session timestamps.

Existing long-option and Treasury holding replay engines now run in
unified offline gate, separate synthetic accounts: physical long put,
cash-settled long call, explicit reference availability, delivery
receivable, cash reconciliation; coupon accrual/receivable/principal
maturity and NAV conservation. No uncovered short assignment, early
exercise, corporate bond default, or general cross-asset OMS settlement.
Existing mixed basket OMS remains independently tested.
Queue/checkpoint temporary file flush+fsync before atomic replace;
injected fsync failure leaves old state. Directory metadata power-loss
durability and broker recovery not independently verified.
Candidate basket risk policy/stress/account snapshot must match across
plans. Registry comparison allows different seeds/models with matching
dataset/evaluation dates and cost/risk policy; descriptive only, holdout
reuse for selection prohibited; positive comparison added to synthetic
registry test. No operational model promotion or reactivation.

Expected 1116 tests = 1112 + 4, not measured yet. Final gate:
```powershell
python scripts/maintenance/verify_release.py --minimum-passed 1116
python scripts/maintenance/benchmark_unified_data.py --offline
```
Stop feature expansion after this batch; only correct actual defects
before local Windows acceptance. Full latest CI pending; no release tag
or merge, #94 frozen. Formal training/backtest/live disabled.
Remaining blockers: generalized cross-asset covariance/allocation and
OMS maturity/assignment, complete automated specialist generation and
actual provider inference, licensed data/identity/PIT, real broker
recovery/kill switch, independent operational approval, current exact
SHA CI and Windows local acceptance. Existing provider configuration
and staged actual API/AI/Paper smoke instructions remain canonical.


## Offline V1.0 — final audit handoff (scope frozen, NOT approved)

### Evidence locked to the tested SHA

Independently fetched all seven successful workflow runs for
5f85f4fdd6bbf7d040390c828f2451db63b7cefb (48 commits, 46 changed files).
Full release evidence, not projected counts:

| Measurement | Windows Python 3.14 | Linux Python 3.12 full optional |
| --- | ---: | ---: |
| Tests passed / executed | 1116 / 1116 | 1116 / 1116 |
| Failures / errors / skips | 0 / 0 / 0 | 0 / 0 / 0 |
| Test runner seconds | 95.743 | 54.934 |
| Offline status | OFFLINE_FIXTURE_PASS | OFFLINE_FIXTURE_PASS |
| Offline wall seconds | 3.1707543 | 2.250121967 |
| Offline CPU seconds | 3.125 | 2.225579273 |
| Process lifetime peak RSS bytes | 152850432 | 220434432 |
| OS process read / write bytes | 22654421 / 295062 | 0 / 389120 |
| Duplicate function body candidates | 0 | 0 |

Windows: https://github.com/yyryang-source/AtlasPM/actions/runs/45514320100/job/138869730104
Linux: https://github.com/yyryang-source/AtlasPM/actions/runs/45514320029/job/138869729022

The Windows I/O counters include non-disk transfers; Linux actual I/O bytes
may exclude cached reads. This workload is bounded synthetic software
acceptance, not a sustained-market performance or physical disk test. The
wall time is higher than e46d83c on both CI hosts; one sample per host does
not establish a regression or improvement. No claim of peak RSS for training.

Final audit corrections after that SHA: orchestrator status distinguishes
metadata topics from actual local specialist dispatch receipts; blocked local
receipts contribute to the blocked count; completed events without local
receipts are explicit. These are cumulative receipt counts, not unique
topics or proof of complete professional coverage. A blocked direct
research CLI request now exits 1, with machine-readable status; exit 0
means only the requested local review returned, not certified research.
Existing metadata queue completion and retry semantics are unchanged.
Added a regression test using real dispatch (no provider calls).
Both full dependency CI jobs now invoke verify_release with minimum 1117;
the gate rejects any skipped/failed/errored tests, source audit errors and
duplicate function body candidates. Latest head results must be read
independently; 1117 is expected, not measured by this document.

### Capability inventory and release boundary

| Capability | Canonical implementation / observed scope | Remaining gate |
| --- | --- | --- |
| Technical | intelligence._local_data_review -> unified equity_daily -> existing stock screener; actual bound event test | Verified identity, data rights, freshness, PIT and numerical forecast validation |
| Fundamental | unified iter_fundamentals / SEC filings; evidence hashes, not general automatic fundamental analyst | Independent filing availability, actual dataset and analyst model |
| News / sentiment | news_intelligence licensed local metadata review; explicit dispatcher | Actual authorized Yahoo/Bloomberg feed, rights, publication time and verified identity; no invented vendor endpoint |
| Options anomaly | options_intelligence bounded UW flow and context adapter | Authorized actual API schema, flow/Greek units and licensed data; no automatic event binding to flow retrieval |
| Investment bank | institutional_research factors, revisions, local licensed import | Actual report access and independent estimate basis/identity checks |
| Credit ratings | credit_intelligence history and XpressAPI adapter | Actual entitlement, issuer/issue matching, effective versus published times |
| Bonds | local bond_quotes receipt, existing Treasury replay and independent DV01/risk | Reliable tradable marks, actual settlement, credit/default and common OMS lifecycle |
| Macro / regime | FRED vintage and Fed disclosures adapters, normalized event metadata | Actual vintage and publication audit; not complete automatic macro/regime research |
| Industry / factors | normalized index events, factor reader and admission structural audit | Licensed classifications/membership and independently certified factor data; no automatic industry model |
| Model inference / fusion | explicit local artifact route, public-evidence external adapter, contract and signal fusion | Real model calls and verified evidence; automatic local receipt-to-numeric fusion not complete |
| Autonomous decision | autonomous_decision, unified portfolio, continuous synthetic entry/exit | Real approved forecasts, identity and independent validation; no trading authorization |
| Cross-asset allocation | exact-leg institutional batch, independent risk, rank_synthetic_baskets, asset-specific risk diagnostics | General joint asset allocation/covariance optimizer (not just candidate comparison), robust Greeks/duration/credit and executable pricing |
| OMS / recovery | existing paper_oms, persisted event prefix, partial fill, cancellation, UNKNOWN halts, after-fill reconciliation | Actual broker recovery, disconnect/restart/reconciliation and kill switch; no blind resubmission |
| Expiry / settlement | real existing option and Treasury replay algorithms exercised in separate fixture accounts | Unified OMS expiry, early exercise, short assignment, corporate default and settlement integration |
| Governance | alpha registry lifecycle, comparison, drift review and suspend/retire; synthetic validation only | Independent operational approval, rollback/revalidation and new untouched validation evidence; no self-approval |
| Security / resources | fixed gateway scope, local secrets, explicit Paper gates, bounded workload, source and duplicate audit | Actual production load and security review; no new permissions or claims of exhaustive security certification |

Unverified capabilities must remain blocked rather than be substituted with
fabricated returns or provider responses. The queue's synthetic metadata
contracts do not certify all specialist algorithms; fundamental/SEC/bond/factor
local data paths currently return descriptive evidence receipts, not verified
numeric forecasts. The bounded offline fixture is an integration gate, not
a generic research scheduler, full cross-asset optimizer or live trading system.

### Consolidated Windows acceptance (after exact-head CI)

Reuse the existing review checkout and interpreter with all required optional
dependencies. Do not modify the main data-download checkout or #94. Choose
the exact reviewed Git SHA from the PR head; never silently switch to a newer
unreviewed head. No financial data downloads, external AI or broker orders.

```powershell
$ErrorActionPreference = "Stop"
$root = "C:\Users\Kevin\Desktop\AtlasPM"
$review = "C:\Users\Kevin\Desktop\AtlasPM-data-review"
$py = Join-Path $root ".venv\Scripts\python.exe"
Set-Location $review
git fetch origin
if ($LASTEXITCODE -ne 0) { throw "Fetch failed" }
$sha = (git rev-parse origin/feature/unified-api-gateway).Trim()
if ($LASTEXITCODE -ne 0) { throw "Head resolution failed" }
Write-Host "Verify this SHA against the completed GitHub CI: $sha"
# Replace only with the exact independently reviewed SHA, if different.
git switch --detach $sha
if ($LASTEXITCODE -ne 0) { throw "Checkout failed" }
$env:PYTHONPATH = Join-Path $review "src"
Remove-Item Env:ATLASPM_ALLOW_PAPER_ORDERS -ErrorAction SilentlyContinue
Remove-Item Env:ATLASPM_ALLOW_EXTERNAL_AI -ErrorAction SilentlyContinue
& $py -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependencies failed" }
& $py scripts/maintenance/verify_release.py --minimum-passed 1117
if ($LASTEXITCODE -ne 0) { throw "Release gate failed" }
& $py scripts/maintenance/benchmark_unified_data.py --offline
if ($LASTEXITCODE -ne 0) { throw "Offline benchmark failed" }
& $py -m atlaspm.data.api_gateway
if ($LASTEXITCODE -ne 0) { throw "Provider inventory failed" }
& $py -m atlaspm.research.intelligence --list
if ($LASTEXITCODE -ne 0) { throw "Model inventory failed" }
& $py -m atlaspm.framework.orchestrator status
if ($LASTEXITCODE -ne 0) { throw "Status failed" }
```

For Linux full optional dependencies, from the checkout root run the same
verify_release and benchmark commands. Full gate requires zero skips.
Unified offline startup:
python -m atlaspm.framework.orchestrator offline --checkpoint local_data/runs/offline_v1_review.continuous.json
Use a new checkpoint for changed fixture inputs; never erase real journals.
Bounded local event worker:
python -m atlaspm.framework.orchestrator run --file local_data/research_bindings.json --cycles 10 --interval 60
Use status, stop, takeover, resume for explicit local control; no broker
or external model calls from this worker. A blocked local research CLI returns
nonzero. The worker is not a production daemon and does not interrupt an
already in-flight broker request.

### Actual connection acceptance (separate stage; no simulated substitutes)

1. **Credentials and rights:** use the consolidated provider table above.
   Official sources: SEC contact, FRED_API_KEY, public Treasury; optional
   MASSIVE_API_KEY. Licensed sources: UW_TOKEN, XPRESSAPI_TOKEN or approved
   XPRESSAPI_USERNAME/PASSWORD, FINRA_CLIENT_ID/SECRET, respective rights,
   retention and bounded quota variables. Yahoo/Bloomberg/investment bank
   and index feed contracts require actual schemas; no assumed API access.
   IBKR uses a local authenticated Paper session, trusted TLS and entitlements.
   External AI uses OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, explicit
   model ID and reviewed public evidence. Never commit keys or private data.
   Check inventory presence only; rights attestations are not independent
   legal certification. Keep ATLASPM_ALLOW_EXTERNAL_AI and
   ATLASPM_ALLOW_PAPER_ORDERS unset until separate authorized tests.
2. **Real data admission:** document provider, rights/retention/AI permissions,
   immutable file hashes, timezone, observation/publication/receipt and
   effective times, vintage, currency, security identity and adjustments;
   reconcile duplicates, missing sessions, survivorship, corporate actions
   and independent price samples. Reuse the existing local admission and
   unified reader, not a second archive. For factor structural audit only:
   python -m atlaspm.research.training_admission --months YYYY-MM --source hf
   An independent reviewer must sign the dataset/identity/PIT/evaluation
   manifest before formal training or backtesting can be considered; this
   CLI does not grant those permissions.
3. **Real AI inference:** start with inventory; one authorized public
   excerpt and bounded provider call via
   python -m atlaspm.research.intelligence --provider openai --model MODEL_ID --input local_data/reviewed_public_evidence.json --allow-external
   with ATLASPM_ALLOW_EXTERNAL_AI=1 only for the reviewed invocation;
   repeat separately for anthropic/gemini if entitled. Verify exact provider
   and reported model, evidence quotes, publication/receipt cutoff, actual
   usage and error/limit behavior. Revoke environment opt-in afterwards.
   Local artifact inference uses orchestrator research with local_artifact
   route and existing content-addressed artifact ID/registry; verify hash,
   input shape, timestamps, real output and holdout independence. Neither
   a valid quote nor a fixture result proves forecast validity.
4. **IBKR Paper (manual, separate approval):** follow
   [IBKR_READONLY](quant/IBKR_READONLY.md) and
   [MARKET_CLOSE_REBALANCE](quant/MARKET_CLOSE_REBALANCE.md).
   Authenticate dedicated Paper DU account locally; verify trusted TLS,
   actual positions/cash/open orders, conid/units, live entitlement and
   timestamps. Review a tiny fixed equity-only Paper plan with current
   market audit; existing ibkr_equity_snapshot, ibkr_whatif and
   ibkr_next_day steps must produce approved exact-leg artifacts. Confirm
   run ID and account manually; submit only through ibkr_paper_orders
   with explicit run confirmation and temporary ATLASPM_ALLOW_PAPER_ORDERS=1.
   Existing quantity/notional/DU limits are mandatory. Reconcile with
   ibkr_paper_orders reconcile and use scoped cancel if necessary.
   Test complete fill, partial fill/cancel, timeout after send, unknown
   response, process death, disconnect/reconnect, duplicate execution ID,
   open order and position/cash mismatch. For unknown outcome halt,
   reconcile customer order ID and executions against broker; never blindly
   retry. Stop/takeover and kill switch require actual independent broker
   verification. Store redacted results and reviewed hashes locally.
   No account login, provider call, order, or real recovery test performed
   by this offline audit.

### Release decision

PR #95 remains a draft candidate; no merge, release tag, production
authorization or formal model approval. PR #94 remains frozen at
5b331ea05c9199c2be3ad8c8f7e877d1bde80174. Before approval, verify exact
new SHA on all CI workflows, local Windows acceptance, source/permission
audit and the unresolved capability criteria above. Formal training,
formal backtesting and automatic live trading remain disabled. Synthetic
training/replay tests do not grant those permissions. No new framework,
no risk relaxation, no extra synthetic provider substitute. Freeze
feature expansion; only defects and explicit missing release requirements
remain in scope.
