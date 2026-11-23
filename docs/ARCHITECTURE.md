# AtlasPM architecture and boundaries

AtlasPM is a **local-first research toolkit**, not a production trading service. It is organized as one Python package plus a Windows-friendly CLI.

```text
src/atlaspm/
  cli.py                 # public command interface, JSON/error handling
  commands/              # selection_cli.py, quant_cli.py, broker_cli.py (CLI adapters)
  framework/             # machine-readable eight-stage quant workflow (NO execution)
  data/                  # quote ingestion/universe, point-in-time and action audits
  research/              # AI/ML studies, stock ranking and walk-forward evidence
  strategies/equity/     # screening, consensus, candidate gates
  risk/                  # margin risk, RiskNet/FusionNet and stress testing
  execution/             # synthetic OMS, ledger and read-only IBKR
  monitoring/            # offline performance, dashboard and audit
  quant/                 # optimizer/backtests + compatibility aliases
  selection/             # legacy compatibility aliases
  strategies/options/    # planned: no implemented options strategy
  strategies/bonds/      # planned: no implemented bond strategy
  portfolio/             # reserved; root portfolio.py legacy module still exists
  hedging/               # planned: no standalone hedge engine
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
5. **IBKR integration** is currently read-only or synthetic. Mapping, financing and stress results are *not independently authenticated broker purchasing power*.
6. **OMS** only replays synthetic events. No real order submission, automatic portfolio rebalancing or live execution is enabled.

## Rules that must not regress

- Never push `local_data/`, broker credentials or unlicensed downloaded market data.
- Keep raw price files immutable; explicit, verified corrections create **new** research copies.
- A bad/stale/tied rank must fail closed. Reductions and exits may not be blocked by an alpha-entry allowlist.
- Do not convert rank utilities into fabricated return probabilities or ignore transaction costs and delistings.
- Broker execution requires a separately built, audited authorization and order lifecycle; none is present today.
- Preserve import compatibility aliases until a versioned API migration updates consumers and regression tests.

For current work, see [QUANT_WORKFLOW.md](QUANT_WORKFLOW.md), [ROADMAP.md](ROADMAP.md), [selection docs](selection/) and [quant docs](quant/).
