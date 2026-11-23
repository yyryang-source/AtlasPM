# AtlasPM documentation

The canonical code lives in two packages:

- **[Selection](selection/)** — [market data QC](selection/US_MARKET_DATA_EXPANSION.md), [V2 learning-to-rank](selection/AI_STOCK_RANKER_V2.md), [monthly candidates](selection/MONTHLY_ALPHA_SELECTION.md), [alpha research gate](selection/ALPHA_ELIGIBILITY_GATE.md), and [corporate-action corrections](selection/CORPORATE_ACTION_PRICE_RECONCILIATION.md).
- **[Quant and risk](quant/)** — [native FusionNet](quant/ATLAS_FUSIONNET_V1.md), [RiskNet](quant/ATLAS_RISKNET_V2.md), [backtesting](quant/BACKTEST.md), [IBKR read-only](quant/IBKR_READONLY.md), and [paper OMS](quant/PAPER_OMS.md).

Shared: [Source Code Layout](CODE_LAYOUT.md), [Standard Quant Workflow](QUANT_WORKFLOW.md), [Architecture](ARCHITECTURE.md), [Data](DATA.md), [Roadmap](ROADMAP.md), [Open-source model review](OPEN_SOURCE_MODELS.md).

Older version-numbered research notes remain under `docs/quant/` for method and regression reproducibility; they are **not separate supported applications**. See the root [README](../README.md) for current status and commands.

All historical research remains non-executable: ranking candidates, calculating weights or passing an offline risk check **does not authorize brokerage orders**.
