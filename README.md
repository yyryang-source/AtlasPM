# AtlasPM

**Open-source, local-first portfolio research for cash and margin accounts.** AtlasPM includes stock screening, experimental AI risk models, historical backtesting, portfolio risk audits, and **read-only / synthetic** IBKR workflows.

> **Status: v0.7.0 research preview.** This is not a robo-adviser or a live-trading application. **Live orders, broker-driven rebalancing, automated stock-to-portfolio handoff, and verified margin authorization are disabled.** Do not treat historical backtests as investable performance or investment advice.

## Start here (Windows PowerShell)

```powershell
git clone https://github.com/yyryang-source/AtlasPM.git
cd AtlasPM
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[ml,native,calendar]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli --help
.\.venv\Scripts\python.exe -m atlaspm.cli analyze --file examples\portfolio.json
```

For existing clones, run `git pull --ff-only origin main` instead of cloning again. Optional market data ingestion needs `pip install -e ".[market]"` (or include `market` with the extras above).

## Where things live

```text
src/atlaspm/
  cli.py            # public entry point (offline research)
  commands/         # 3 public *_cli facades + 11 focused command families
  framework/        # 8-stage quant workflow registry (inspection only)
  data/            # stock quotes, history, universe and quality (migrated)
  research/        # AI ranking, model validation and walk-forward (migrated)
  strategies/
    equity/         # stock screener, candidate gate, monthly strategy (migrated)
    options/        # planned: options research (no implementation)
    bonds/          # planned: bond research (no implementation)
  risk/             # risk models, stress scenarios and margin controls (migrated)
  execution/        # synthetic OMS, read-only IBKR and ledger (migrated)
  monitoring/       # historical reports, audits and local dashboard (migrated)
  portfolio/        # reserved: root portfolio.py legacy module takes precedence
  hedging/          # planned: no hedge engine implementation
  quant/            # core backtest/optimizer + compatible legacy import aliases
  selection/        # compatible legacy import aliases
tests/
  quant/            # quantitative/risk regression tests
  selection/        # research ranking/data-quality regression tests
scripts/selection/  # canonical Windows PowerShell runners
docs/
  quant/            # portfolio, finance and risk methods
  selection/        # stock selection, data quality and ranking
examples/           # synthetic inputs only
```

**Source code is now reclassified by domain**, with old import paths retained
as compatibility aliases. See [complete source map](docs/CODE_LAYOUT.md).
This code organization does **not** implement portfolio/hedging/options/bonds
engines or enable live broker execution.

Legacy import aliases such as `atlaspm.backtest` remain temporarily for compatibility with existing tests and external scripts. **New code should use `atlaspm.quant.*` and `atlaspm.selection.*`.**

## Local market database

An offline, gitignored SQLite warehouse now accepts the existing equity adjusted-close CSV, Massive option contract/day/5m/1m candle CSVs and US Treasury FiscalData auction CSVs. Use `python -m atlaspm.cli market-db init`, then `market-db import-stocks`, and inspect `market-db status`. See [Windows market database guide](docs/MARKET_DATABASE.md). No real market data are uploaded to GitHub; no live trading is enabled.

## Primary workflows

| Goal | Entry point | Scope |
| --- | --- | --- |
| Explore portfolio risk | `atlaspm analyze --file examples/portfolio.json` | Cash / margin **research** |
| Run historical backtests | `atlaspm backtest --help` | Simulated, not executed |
| Train native risk models | `atlaspm atlas-native-backtest --help` | Offline, 0–1× cash exposure |
| Research US stock candidates | `atlaspm stock-screen --help` | Data-quality dependent |
| Train stock ranking models | `atlaspm stock-ai-train-v2 --help` | Experimental; not approved alpha |
| Month-end candidate research | `scripts/selection/Invoke-MonthlyStockResearch.ps1` | No orders or handoff |
| Audit corporate-action data | `atlaspm stock-split-reconcile --help` | Raw file preserved |
| Simulate IBKR risk | `atlaspm ibkr-stress --help` | Offline / synthetic |

**Current evidence limits:** The expanded current-listed US dataset has about 5,830 cached symbols, but it is not a historical point-in-time universe; it is vulnerable to survivorship bias. In the first full-market LightGBM V2 experiment, held-out Rank IC was **-0.01774** versus **+0.11176** for a simple momentum baseline, and Top-10 scores tied across decision dates. This model is **not eligible for automatic trading**. The separate RiskNet/FusionNet historical results have also been examined repeatedly and require independent validation.

## Standard quantitative workflow (research specification)

The target architecture is **Data & governance → Alpha research →
independent validation → equity / options / bonds sleeves →
cross-asset portfolio optimization + hedging + continuous risk →
pretrade controls → OMS & reconciliation → monitoring + research feedback**.

The [GitHub Mermaid flowchart](docs/QUANT_WORKFLOW.md) and read-only
`python -m atlaspm.framework --format json` command show the current
research-vs-planned status. **Options/bonds engines, unified hedge optimizer,
real-time execution and live broker permissions are not implemented.**

## Documentation

- [Architecture and safety boundaries](docs/ARCHITECTURE.md)
- [Standard quantitative trading flowchart](docs/QUANT_WORKFLOW.md)
- [Command interface and module layout](docs/CLI.md)
- [Canonical source-code map](docs/CODE_LAYOUT.md)
- [Documentation index](docs/README.md)
- [Stock selection and ranking](docs/selection/AI_STOCK_RANKER_V2.md)
- [Market data coverage](docs/selection/US_MARKET_DATA_EXPANSION.md)
- [Split reconciliation](docs/selection/CORPORATE_ACTION_PRICE_RECONCILIATION.md)
- [Research-only alpha eligibility](docs/selection/ALPHA_ELIGIBILITY_GATE.md)
- [Native risk models](docs/quant/ATLAS_FUSIONNET_V1.md)
- [Current development roadmap](docs/ROADMAP.md)
- [Security guidelines](SECURITY.md)

## Privacy and contributions

Market data, brokerage snapshots, credentials, training outputs and model artifacts must remain in ignored local folders such as `local_data/`; do **not** commit them. The Git repository contains only code, synthetic fixtures, reproducible tests, documented assumptions and approved small research manifests.

Run `python -m unittest discover -s tests -v` before proposing changes. GitHub Actions runs Linux and Windows checks; no live broker access is needed. Changes to ranking, margin risk or order-preflight behavior must preserve fail-closed defaults. MIT licensed; see [LICENSE](LICENSE).
