# Compatibility-shim usage inventory and retirement

**Retirement update:** The 39 candidates listed below are retired on the lifecycle cleanup branch after maintainer direction to remove unused code. Their canonical implementations are retained. The subsequent 47 commits were checked for newly added direct fully qualified references and `from atlaspm import` statements; no new use of these candidates was found. External callers and arbitrary dynamic imports cannot be proven absent. This is a breaking change for callers of these deprecated paths; use the canonical destination in `pyproject.toml`'s banned-api map. The remaining 59 shims retain warnings and identity checks. Full CI is required before merge.

The original Stage 3 inventory follows for audit history. It was a **static import and known-string-reference inventory**, not a dead-code proof. All **98** shims remain supported and now warn with `DeprecationWarning(stacklevel=2)` when first imported. All **25** multi-hop shims point directly to their canonical module.

After replacing 112 static imports in 58 test files (and 72 in the earlier source migration), direct `src/` and `tests/` import statements reference **zero** shims. Existing compatibility-identity tests intentionally exercise deprecated names through dynamic `importlib` calls, and some tests build names from component strings.

The following **39 retired paths** (original candidates) have no static imports or direct complete-string references in non-shim `src/` and `tests/`, after accounting for the known constructed aliases in `tests/interfaces/test_package_layout.py`. They **might still be in use** by external clients, entry points, documentation, plugin loaders, or other dynamic imports. These were originally marked for review before retirement.

- `atlaspm.atlas_execution_policy`
- `atlaspm.atlas_fusion_allocator`
- `atlaspm.atlas_regimenet`
- `atlaspm.atlas_robustness_audit`
- `atlaspm.audit`
- `atlaspm.backtest`
- `atlaspm.benchmark`
- `atlaspm.broker_reconcile`
- `atlaspm.historical`
- `atlaspm.ibkr_dashboard`
- `atlaspm.ibkr_financing`
- `atlaspm.ibkr_mapping`
- `atlaspm.ibkr_margin_monitor`
- `atlaspm.ibkr_margin_stress`
- `atlaspm.ibkr_multiasset_budget`
- `atlaspm.ibkr_reverse_stress`
- `atlaspm.ibkr_risk_audit_diff`
- `atlaspm.ibkr_risk_chain_audit`
- `atlaspm.institutional_batch`
- `atlaspm.institutional_gate`
- `atlaspm.institutional_ledger`
- `atlaspm.institutional_rebalance`
- `atlaspm.mean_variance`
- `atlaspm.ml_dynamic_rebalance`
- `atlaspm.ml_model_tournament`
- `atlaspm.models`
- `atlaspm.oms_controls`
- `atlaspm.paper_oms`
- `atlaspm.performance`
- `atlaspm.quant.atlas_execution_policy`
- `atlaspm.quant.atlas_fusion_allocator`
- `atlaspm.quant.backtest`
- `atlaspm.quant.benchmark`
- `atlaspm.quant.mean_variance`
- `atlaspm.quant.models`
- `atlaspm.risk_backtest`
- `atlaspm.stock_walkforward`
- `atlaspm.tail_risk`
- `atlaspm.us_market_prices`

## Still referenced intentionally

The other **59** aliases appear as complete string references (e.g. in `tests/interfaces/test_domain_migration.py`) or are assembled dynamically by a known test. They are **not** candidates in this inventory. All 98 are still public compatibility paths regardless of the category above.

## Safety and verification

At original Stage 3 no shim was deleted. The cleanup retires the listed 39 shims only; no canonical implementation, business logic or runtime permission / live-execution boolean changes. Ruff's `TID251` banned-api map blocks explicit legacy imports in normal source files when Ruff is run; it does not detect dynamic `importlib` strings. Stage 5 will wire permanent Ruff CI checks.
