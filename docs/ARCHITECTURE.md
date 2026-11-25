# AtlasPM architecture and boundaries

AtlasPM is a **local-first research toolkit**, not a production trading service. It is organized as one Python package plus a Windows-friendly CLI.

```text
src/atlaspm/
  cli.py                 # public command interface, JSON/error handling
  commands/              # selection_cli.py, quant_cli.py, broker_cli.py (CLI adapters)
  framework/             # eight-stage registry, lifecycle journal and bounded offline orchestration
  data/                  # quote ingestion/universe, point-in-time and action audits
  research/              # AI/ML studies, stock ranking and walk-forward evidence
  strategies/equity/     # screening, consensus, candidate gates
  risk/                  # margin risk, RiskNet/FusionNet and stress testing
  execution/             # synthetic OMS, ledger, read-only IBKR and explicit manual Paper
  monitoring/            # offline performance, dashboard and audit
  quant/                 # compatibility aliases; canonical algorithms in engine/research/risk
  selection/             # legacy compatibility aliases
  strategies/options/    # historical option replay and protective puts
  strategies/bonds/      # Treasury price and cash-flow replay
  portfolio/             # reserved; root portfolio.py legacy module still exists
  hedging/               # historical multi-asset hedge research
  *.py                   # temporary compatibility aliases for original module imports
scripts/selection/       # PowerShell runners
tests/quant/             # risk/backtest/regression suite
tests/selection/         # ranking/data-quality regression suite
docs/quant/              # independent risk and research methods
docs/selection/          # stock-selection methods
examples/                # fictional/synthetic data only
```

The [canonical code migration map](CODE_LAYOUT.md) documents source locations
and module-object-preserving legacy imports. Source files were moved without
changing their algorithms; new code should use canonical domain imports.

The [standard quantitative trading architecture](QUANT_WORKFLOW.md) now maps
**Data → Alpha → Validation → Equity/Options/Bonds → Portfolio & Hedging →
Pretrade Gate → Synthetic OMS → Monitoring & Research Feedback**.
The `atlaspm.framework` registry is read-only and does not imply any missing
asset engine, broker permission, or production execution has been implemented.

## Research flow

1. **Data ingestion** obtains a current listing snapshot and local quotes. Audits reject incomplete/stale prices and ambiguous corporate actions. Today's listing snapshot **cannot recreate historic delistings**.
2. **Stock selection** computes deterministic factors or fits LightGBM ranking models on past-only examples. Model and Top-K ties must be evaluated before interpreting any alpha.
3. **Alpha eligibility** produces a dated **research allowlist**, currently not connected to live allocation; it never authorizes orders or borrowing.
4. **Portfolio and risk** run offline research backtests (cash/margin scenarios) and independent native AI risk models. Native fitted model backtests remain restricted to **0–1× cash exposure**.
5. **IBKR integration** includes read-only and explicitly human-confirmed Paper submission. Mapping, financing and stress results are *not independently authenticated broker purchasing power*.
6. **OMS** replays synthetic events; the separate Paper workflow requires explicit operator confirmation. Real-account orders and automatic broker execution remain disabled.

## Rules that must not regress

- Never push `local_data/`, broker credentials or unlicensed downloaded market data.
- Keep raw price files immutable; explicit, verified corrections create **new** research copies.
- A bad/stale/tied rank must fail closed. Reductions and exits may not be blocked by an alpha-entry allowlist.
- Do not convert rank utilities into fabricated return probabilities or ignore transaction costs and delistings.
- Live broker execution requires separate independently verified authorization. The existing manual Paper path is isolated and retains every existing limit.
- Preserve import compatibility aliases until a versioned API migration updates consumers and regression tests.

For current work, see [QUANT_WORKFLOW.md](QUANT_WORKFLOW.md), [ROADMAP.md](ROADMAP.md), [selection docs](selection/) and [quant docs](quant/).


## Autonomous Alpha research (stage 4, incremental)

See [Autonomous Alpha Engine](AUTONOMOUS_ALPHA_ENGINE.md) for implemented synthetic research, safety boundaries, CLI and remaining validation. Run `python -m atlaspm.cli autonomous-alpha --file local_data/alpha_request.json`. Production admission and live execution remain disabled.

## Offline foundation acceptance

Run `python -m atlaspm.cli architecture` to inspect the current CLI registry,
8-stage workflow, 7-phase/21-step lifecycle, mapped canonical modules and
permission flags. It does not read private data or call providers. The full
release verifier also requires this contract to pass.

The lifecycle review journal persists **declared** receipts locally, verifies
all transitions on reopen, requires an expected revision for append, and
allows only an exact immediate retry after a lost response. Writer conflict
or failure preserves the prior state. Use lowercase portable review IDs.
The digest is corruption detection, not a signature or independent evidence
approval. A stale writer lock after process death is fail-closed and requires
operator investigation; never remove a lock while a writer is active.
No production admission or broker authority follows from a completed review.
See [CLI](CLI.md) for the entry points.
