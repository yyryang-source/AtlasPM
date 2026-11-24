# Canonical code layout

AtlasPM groups source files by domain. Legacy `atlaspm.selection.*`, `atlaspm.quant.*` and top-level compatibility imports use module-identity aliases for the relocated implementations.

| Package | Functionality | Status |
| --- | --- | --- |
| `atlaspm.data` | Price histories, universe, quote QA, split events | Research code moved |
| `atlaspm.research` | AI rankers, walk-forward, model experiments | Research code moved |
| `atlaspm.strategies.equity` | Stock screen, 20 signals, monthly candidates, alpha gate | Research code moved |
| `atlaspm.risk` | RiskNet/FusionNet, financing, stress, risk budget | Research code moved |
| `atlaspm.execution` | Offline ledger, preflight, synthetic OMS, IBKR read-only and human-confirmed Paper orders | Live-account orders disabled |
| `atlaspm.monitoring` | Historical performance, audits, HTML risk snapshot | Offline only |
| `atlaspm.engine` | Unified portfolio, allocation, historical replay and analytics | Synthetic research |
| `atlaspm.commands` | Stable CLI adapters and domain dispatch | Existing |
| `atlaspm.framework` | Standard research workflow orchestration | Existing |
| `atlaspm.quant` | Compatibility aliases only; algorithms live in their canonical domains | Compatibility |
| `atlaspm.selection` | Legacy import aliases | Existing |
| `atlaspm.strategies.options` | Historical options and protective put replay | Research |
| `atlaspm.strategies.bonds` | Treasury pricing and cash flow replay | Research |
| `atlaspm.hedging` | Historical equity, Treasury and protective put comparisons | Research |
| `portfolio/` | Reserved for future package use | Unified equity engine lives in `atlaspm.engine` |

## Migrated source files

- `atlaspm.selection.us_market_universe` → `atlaspm.data.us_market_universe`
- `atlaspm.selection.us_market_prices` → `atlaspm.data.us_market_prices`
- `atlaspm.selection.us_market_quality` → `atlaspm.data.us_market_quality`
- `atlaspm.selection.price_arrays` → `atlaspm.data.price_arrays`
- `atlaspm.selection.recent_prices` → `atlaspm.data.recent_prices`
- `atlaspm.selection.corporate_action_reconciliation` → `atlaspm.data.corporate_action_reconciliation`
- `atlaspm.quant.historical` → `atlaspm.data.historical`
- `atlaspm.selection.ai_stock_ranker` → `atlaspm.research.ai_stock_ranker`
- `atlaspm.selection.ai_stock_ranker_v2` → `atlaspm.research.ai_stock_ranker_v2`
- `atlaspm.selection.stock_walkforward` → `atlaspm.research.stock_walkforward`
- `atlaspm.selection.v2_momentum_diagnostics` → `atlaspm.research.v2_momentum_diagnostics`
- `atlaspm.quant.ml_dynamic_rebalance` → `atlaspm.research.ml_dynamic_rebalance`
- `atlaspm.quant.ml_model_tournament` → `atlaspm.research.ml_model_tournament`
- `atlaspm.quant.atlas_regimenet` → `atlaspm.research.atlas_regimenet`
- `atlaspm.quant.atlas_robustness_audit` → `atlaspm.research.atlas_robustness_audit`
- `atlaspm.selection.stock_screener` → `atlaspm.strategies.equity.stock_screener`
- `atlaspm.selection.consensus_20_screener` → `atlaspm.strategies.equity.consensus_20_screener`
- `atlaspm.selection.monthly_stock_selection` → `atlaspm.strategies.equity.monthly_stock_selection`
- `atlaspm.selection.alpha_eligibility` → `atlaspm.strategies.equity.alpha_eligibility`
- `atlaspm.quant.risk_budget` → `atlaspm.risk.risk_budget`
- `atlaspm.quant.risk_backtest` → `atlaspm.risk.risk_backtest`
- `atlaspm.quant.tail_risk` → `atlaspm.risk.tail_risk`
- `atlaspm.quant.atlas_risknet` → `atlaspm.risk.atlas_risknet`
- `atlaspm.quant.atlas_fusionnet` → `atlaspm.risk.atlas_fusionnet`
- `atlaspm.quant.ibkr_margin_stress` → `atlaspm.risk.ibkr_margin_stress`
- `atlaspm.quant.ibkr_reverse_stress` → `atlaspm.risk.ibkr_reverse_stress`
- `atlaspm.quant.ibkr_multiasset_budget` → `atlaspm.risk.ibkr_multiasset_budget`
- `atlaspm.quant.ibkr_financing` → `atlaspm.risk.ibkr_financing`
- `atlaspm.quant.ibkr_margin_monitor` → `atlaspm.risk.ibkr_margin_monitor`
- `atlaspm.quant.paper_oms` → `atlaspm.execution.paper_oms`
- `atlaspm.quant.oms_controls` → `atlaspm.execution.oms_controls`
- `atlaspm.quant.institutional_gate` → `atlaspm.execution.institutional_gate`
- `atlaspm.quant.institutional_ledger` → `atlaspm.execution.institutional_ledger`
- `atlaspm.quant.institutional_batch` → `atlaspm.execution.institutional_batch`
- `atlaspm.quant.broker_reconcile` → `atlaspm.execution.broker_reconcile`
- `atlaspm.quant.institutional_rebalance` → `atlaspm.execution.institutional_rebalance`
- `atlaspm.quant.ibkr_mapping` → `atlaspm.execution.ibkr_mapping`
- `atlaspm.quant.ibkr_gateway` → `atlaspm.execution.ibkr_gateway`
- `atlaspm.quant.performance` → `atlaspm.monitoring.performance`
- `atlaspm.quant.ibkr_dashboard` → `atlaspm.monitoring.ibkr_dashboard`
- `atlaspm.quant.ibkr_risk_chain_audit` → `atlaspm.monitoring.ibkr_risk_chain_audit`
- `atlaspm.quant.ibkr_risk_audit_diff` → `atlaspm.monitoring.ibkr_risk_audit_diff`
- `atlaspm.quant.audit` → `atlaspm.monitoring.audit`

New code should import canonical paths. `atlaspm/portfolio.py` is the compatibility module for the unified equity engine, and the `portfolio/` directory is reserved. The local workflow entry points and Paper validation scope are listed in [FUNCTIONS.md](FUNCTIONS.md).

## Maintenance rules

- Use canonical domain modules for new implementations. Legacy import shims preserve module identity and are not independent models.
- Preserve historical baseline algorithms and benchmark fixtures when tests or comparisons use them. Version numbers alone are not evidence of dead code.
- Do not add a new source copy to a compatibility namespace. Remove empty placeholders from populated packages.
- Keep private data, prediction archives, model checkpoints and temporary feature workspaces outside Git under local_data.
- Run the entire test suite and the focused AI/Parquet/replay CI before merging. Peak RSS must be measured; file counts are not a memory benchmark.

### Original quant implementation relocation

- `atlaspm.quant.atlas_execution_policy` → `atlaspm.execution.atlas_execution_policy`
- `atlaspm.quant.atlas_fusion_allocator` → `atlaspm.engine.atlas_fusion_allocator`
- `atlaspm.quant.backtest` → `atlaspm.engine.backtest`
- `atlaspm.quant.benchmark` → `atlaspm.monitoring.benchmark`
- `atlaspm.quant.mean_variance` → `atlaspm.engine.mean_variance`
- `atlaspm.quant.ml_lightgbm` → `atlaspm.research.ml_lightgbm`
- `atlaspm.quant.models` → `atlaspm.engine.models`
- `atlaspm.quant.optimizer` → `atlaspm.engine.optimizer`
- `atlaspm.quant.portfolio` → `atlaspm.engine.portfolio`

These implementations are moved byte-for-byte; old root and quant import paths resolve to the same canonical module object. Baseline algorithms remain available for reproducible comparisons.


### Paper workflow relocation

- `atlaspm.ibkr_paper_orders` → `atlaspm.execution.ibkr_paper_orders`
- `atlaspm.ibkr_whatif` → `atlaspm.execution.ibkr_whatif`
- `atlaspm.ibkr_next_day` → `atlaspm.execution.ibkr_next_day`
- `atlaspm.ibkr_equity_snapshot` → `atlaspm.execution.ibkr_equity_snapshot`
- `atlaspm.paper_order_journal` → `atlaspm.execution.paper_order_journal`
- `atlaspm.close_drafts` → `atlaspm.execution.close_drafts`
- `atlaspm.market_close` → `atlaspm.execution.market_close`

Existing root IBKR risk, mapping and monitoring modules already resolve
to their canonical domains; no additional implementation copies are
created. The seven relocated root paths remain direct module-identity
aliases with DeprecationWarning. Existing private helper imports, mock
patch targets, and CLI invocations using python -m remain compatible.
New code and documentation use canonical paths.

Human-confirmed Paper submission still requires the reviewed run and
matching --confirm-run. The submission guard additionally requires a
DU account with an ASCII numeric suffix and the exact environment value
ATLASPM_ALLOW_PAPER_ORDERS=1. Limits: 1,000 shares and USD 25,000 limit
notional per ticket; USD 100,000 gross limit notional per batch (both
sides summed). These are submitted limit notionals, not a guarantee
of execution value. Reconciliation and cancellation remain available
without the environment opt-in. Live-account order submission is disabled.
