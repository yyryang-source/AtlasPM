# AtlasPM Documentation

This repository separates two research domains:

- **[Stock selection](selection/)**: stock screener, US universe and quote ingestion, LightGBM AI rankers, calendar-aware monthly selection, and the offline alpha eligibility filter.
- **[Quantitative portfolio and risk](quant/)**: portfolio optimization, Atlas RiskNet / RegimeNet / FusionNet, risk budgets, financing, IBKR reconciliation and synthetic OMS workflows.

Browse the [current feature inventory](FUNCTIONS.md) and track remaining
development and verified milestones in the [AtlasPM task list](TODO.md).

Shared starting points: [Architecture](ARCHITECTURE.md), [Data](DATA.md), [Roadmap](ROADMAP.md) and [Open-source model references](OPEN_SOURCE_MODELS.md).

The stock screener is not a broker order flow. The alpha eligibility file is a research-only candidate universe, not execution authorization.
