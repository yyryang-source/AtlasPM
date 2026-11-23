# Canonical code layout

AtlasPM now classifies existing **source files** into specialized domains. The original Python code was moved byte-for-byte; old `atlaspm.selection.*`, `atlaspm.quant.*` and top-level compatibility imports remain available via small module-identity aliases. This is a code-maintenance refactor, **not a new trading system**.

| Package | Functionality | Status |
| --- | --- | --- |
| `atlaspm.data` | Price histories, universe, quote QA, split events | Research code moved |
| `atlaspm.research` | AI rankers, walk-forward, model experiments | Research code moved |
| `atlaspm.strategies.equity` | Stock screen, 20 signals, monthly candidates, alpha gate | Research code moved |
| `atlaspm.risk` | RiskNet/FusionNet, financing, stress, risk budget | Research code moved |
| `atlaspm.execution` | Offline ledger, preflight, synthetic OMS, read-only IBKR | No live orders |
| `atlaspm.monitoring` | Historical performance, audits, HTML risk snapshot | Offline only |
| `atlaspm.quant` | Portfolio/backtest/optimizer plus legacy import aliases | Existing |
| `atlaspm.selection` | Legacy import aliases | Existing |
| `strategies.options`, `strategies.bonds` | Planned research | No implementation |
| `portfolio/`, `hedging/` | Reserved; portfolio.py root alias remains | No unified multi-asset engine |

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

New code should import canonical paths. Existing CLI flags, JSON schemas, model formulas and risk protections have not changed. The Python source files are identical at their new locations; only import compatibility wrappers have been added. `atlaspm/portfolio.py` remains a module that would shadow a same-named Python package; the `portfolio/` folder is not activated as a package. Hedging/options/bonds strategies are not implemented. **Do not infer improved strategy performance or broker authorization from file movement.**
