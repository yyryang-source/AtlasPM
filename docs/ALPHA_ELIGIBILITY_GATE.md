# AtlasPM Alpha Eligibility Gate — monthly quant-universe threshold

## Separation of responsibilities

**AI selects research candidates; quant/risk decides any actual portfolio
allocation or trade.** This new module does not communicate with IBKR,
construct order quantities, size a position, issue orders or authorize
the existing order manager. No broker flow has been connected.

1. Existing \`stock-ai-monthly\` creates the last completed NYSE-month-end
   stock research report. It consumes a pre-trained V2 \`rank_xendcg\` model,
   whose scores are **ordinal utilities, NOT return percentages or probabilities**.
2. New \`stock-alpha-gate\` reads that JSON, applies a threshold and writes
   a dated **research-only alpha candidate allowlist**.
3. New \`stock-alpha-check\` checks a hypothetical portfolio action.
   \`OPEN\` and \`INCREASE\` require membership in a passing, date-current
   allowlist; \`REDUCE\` and \`CLOSE\` are NOT blocked by alpha, so risk
   reduction is always available independently of the stock ranking.
   \`HOLD\` does not require new-entry alpha eligibility. Separate real
   broker/risk gates are still mandatory for every actual action.

## Default threshold — research policy, NOT optimized parameters

- \`top-percent=10\`: Top 10% of all **scored** equities at that month end.
- \`max-ranked-names=50\`: never more than Top 50, even when 10% contains
  hundreds of companies; output uses the published Top50 only.
- \`minimum-scored-names=500\`: do not let 38 surviving mega-caps
  masquerade as a market-wide screen.
- \`minimum-listing-coverage-pct=90\`: the matched symbol coverage
  against a supplied US current listing manifest. Current symbols do
  **not** make historical data survivorship-bias free.
- \`maximum-model-age-days=180\`: reject old models; do not mechanically
  extend expiry merely to fill the candidate list.
- Monthly \`report_status\` must be \`READY_FOR_RESEARCH_REVIEW\`.
  Model/price freshness, date, manifest and Top50 scope must pass the
  upstream research report checks.
- The threshold must **not** cut through an equal-score group; if it
  would split a tie, the **whole** alpha gate is blocked for review.
- The gate applies **only during the calendar month immediately after
  the report's month-end**. For example, 2026-10 candidates are for
  research in **2026-11**, not 2026-12.

Do not choose a fixed cutoff like \`ranking_score > 0.05\`; V2 scores
are not probabilities, cross-model calibrated expected returns or
stable absolute value scales. Top 10% and Top 50 are initial
configuration defaults; test walk-forward and turnover/cost sensitivity
before treating them as economically justified.

## Windows PowerShell example — October 2026 research

First make sure the month-end report already exists under \`local_data\`.
A Windows PowerShell 5.1 session:

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
git fetch origin
git switch --track origin/feature/ai-alpha-eligibility-gate-v1
# Or if branch exists locally: git switch feature/ai-alpha-eligibility-gate-v1
.\.venv\Scripts\python.exe -m pip install -e ".[ml,calendar]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_alpha_eligibility.py" -v

.\.venv\Scripts\python.exe -m atlaspm.cli stock-alpha-gate `
    --monthly-report local_data\monthly_stock_research\2026-10_monthly_stock_research.json `
    --top-percent 10 `
    --max-ranked-names 50 `
    --output local_data\monthly_stock_research\2026-10_alpha_eligibility.json

$g = Get-Content local_data\monthly_stock_research\2026-10_alpha_eligibility.json -Raw | ConvertFrom-Json
$g.research_eligibility_status
$g.blocked_reasons
$g.eligible_count
$g.eligible_symbols

.\.venv\Scripts\python.exe -m atlaspm.cli stock-alpha-check `
    --gate local_data\monthly_stock_research\2026-10_alpha_eligibility.json `
    --symbol INTC --action OPEN --decision-date 2026-11-02

.\.venv\Scripts\python.exe -m atlaspm.cli stock-alpha-check `
    --gate local_data\monthly_stock_research\2026-10_alpha_eligibility.json `
    --symbol INTC --action CLOSE --decision-date 2026-11-02
```

Expected with actual previously supplied October 2026 report:
\`BLOCKED_DATA_OR_MODEL\` and \`eligible_count=0\`. Upstream report
is REVIEW_REQUIRED because (1) the V2 model is trained only through
2018-12-31, (2) a full-US listing manifest is absent, and (3) only 38
symbols were scored. A new-entry check is denied; a separate risk-
reduction check remains outside the alpha filter. DO NOT change flags
merely to force an eligible stock list from this dataset.

## Contract

\`YYYY-MM_alpha_eligibility.json\` includes:
- \`month\`, \`valid_for_month\`, \`as_of_nyse_session\`;
- \`monthly_report_sha256\`, \`model_sha256\` for provenance/audit (not a
  cryptographic proof of report authenticity);
- \`eligibility_rule\`: percentile cap, Top N cap, minimum eligible
  dataset coverage and maximum acceptable model training age;
- \`eligible_symbols\`, \`new_or_increase_research_candidates\`;
- \`research_eligibility_status\` and \`blocked_reasons\`;
- \`live_execution_enabled=false\`, \`automated_handoff_approved=false\`,
  and \`order_instructions=[]\`.

The quant model can eventually use the symbols as a **universe constraint**
for new/increased exposures. This PR does **NOT** wire that constraint into
a live optimizer or trading engine; doing so requires separate integration,
testing, order-intent schema validation and approval. Exits must remain
possible even if the alpha research gate is blocked.

## Data and validation prerequisites

1. Expand quote coverage from 38 survivors to the intended listed-US scope;
   match quote symbol names with the stock listing manifest.
2. Train/validate models on that broader universe using point-in-time
   eligibility and delisted securities where available; monitor model
   quality, IC and future out-of-sample reliability.
3. Build monthly reports after the NYSE last trading session with
   complete quote data. Refit the model on a schedule that purges the
   unobservable final 21-session labels and evaluate separately.
4. Tune top-percent, max names and turnover in **walk-forward research**;
   no action should be accepted solely because an alpha score is high.

The gate filters the research universe and does not replace independent
capital/margin/position/risk constraints.
