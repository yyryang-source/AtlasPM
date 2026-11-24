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
