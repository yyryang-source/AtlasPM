"""Compatibility checks for source relocation across quant domains."""
from __future__ import annotations
import importlib
from pathlib import Path
import unittest
import atlaspm

MOVED_MODULES = (
    ("atlaspm.selection.us_market_universe", "atlaspm.data.us_market_universe"),
    ("atlaspm.selection.us_market_prices", "atlaspm.data.us_market_prices"),
    ("atlaspm.selection.us_market_quality", "atlaspm.data.us_market_quality"),
    ("atlaspm.selection.price_arrays", "atlaspm.data.price_arrays"),
    ("atlaspm.selection.recent_prices", "atlaspm.data.recent_prices"),
    ("atlaspm.selection.corporate_action_reconciliation", "atlaspm.data.corporate_action_reconciliation"),
    ("atlaspm.quant.historical", "atlaspm.data.historical"),
    ("atlaspm.selection.ai_stock_ranker", "atlaspm.research.ai_stock_ranker"),
    ("atlaspm.selection.ai_stock_ranker_v2", "atlaspm.research.ai_stock_ranker_v2"),
    ("atlaspm.selection.stock_walkforward", "atlaspm.research.stock_walkforward"),
    ("atlaspm.selection.v2_momentum_diagnostics", "atlaspm.research.v2_momentum_diagnostics"),
    ("atlaspm.quant.ml_dynamic_rebalance", "atlaspm.research.ml_dynamic_rebalance"),
    ("atlaspm.quant.ml_model_tournament", "atlaspm.research.ml_model_tournament"),
    ("atlaspm.quant.atlas_regimenet", "atlaspm.research.atlas_regimenet"),
    ("atlaspm.quant.atlas_robustness_audit", "atlaspm.research.atlas_robustness_audit"),
    ("atlaspm.selection.stock_screener", "atlaspm.strategies.equity.stock_screener"),
    ("atlaspm.selection.consensus_20_screener", "atlaspm.strategies.equity.consensus_20_screener"),
    ("atlaspm.selection.monthly_stock_selection", "atlaspm.strategies.equity.monthly_stock_selection"),
    ("atlaspm.selection.alpha_eligibility", "atlaspm.strategies.equity.alpha_eligibility"),
    ("atlaspm.quant.risk_budget", "atlaspm.risk.risk_budget"),
    ("atlaspm.quant.risk_backtest", "atlaspm.risk.risk_backtest"),
    ("atlaspm.quant.tail_risk", "atlaspm.risk.tail_risk"),
    ("atlaspm.quant.atlas_risknet", "atlaspm.risk.atlas_risknet"),
    ("atlaspm.quant.atlas_fusionnet", "atlaspm.risk.atlas_fusionnet"),
    ("atlaspm.quant.ibkr_margin_stress", "atlaspm.risk.ibkr_margin_stress"),
    ("atlaspm.quant.ibkr_reverse_stress", "atlaspm.risk.ibkr_reverse_stress"),
    ("atlaspm.quant.ibkr_multiasset_budget", "atlaspm.risk.ibkr_multiasset_budget"),
    ("atlaspm.quant.ibkr_financing", "atlaspm.risk.ibkr_financing"),
    ("atlaspm.quant.ibkr_margin_monitor", "atlaspm.risk.ibkr_margin_monitor"),
    ("atlaspm.quant.paper_oms", "atlaspm.execution.paper_oms"),
    ("atlaspm.quant.oms_controls", "atlaspm.execution.oms_controls"),
    ("atlaspm.quant.institutional_gate", "atlaspm.execution.institutional_gate"),
    ("atlaspm.quant.institutional_ledger", "atlaspm.execution.institutional_ledger"),
    ("atlaspm.quant.institutional_batch", "atlaspm.execution.institutional_batch"),
    ("atlaspm.quant.broker_reconcile", "atlaspm.execution.broker_reconcile"),
    ("atlaspm.quant.institutional_rebalance", "atlaspm.execution.institutional_rebalance"),
    ("atlaspm.quant.ibkr_mapping", "atlaspm.execution.ibkr_mapping"),
    ("atlaspm.quant.ibkr_gateway", "atlaspm.execution.ibkr_gateway"),
    ("atlaspm.quant.performance", "atlaspm.monitoring.performance"),
    ("atlaspm.quant.ibkr_dashboard", "atlaspm.monitoring.ibkr_dashboard"),
    ("atlaspm.quant.ibkr_risk_chain_audit", "atlaspm.monitoring.ibkr_risk_chain_audit"),
    ("atlaspm.quant.ibkr_risk_audit_diff", "atlaspm.monitoring.ibkr_risk_audit_diff"),
    ("atlaspm.quant.audit", "atlaspm.monitoring.audit"),
)

class DomainMigrationTests(unittest.TestCase):
    def test_migrated_modules_preserve_old_module_identity(self):
        self.assertEqual(len(MOVED_MODULES), 43)
        for old, new in MOVED_MODULES:
            with self.subTest(old=old):
                new_module = importlib.import_module(new)
                self.assertIs(importlib.import_module(old), new_module)
                self.assertTrue(Path(new_module.__file__).is_file())

    def test_root_compatibility_imports(self):
        for old, new in MOVED_MODULES:
            root = "atlaspm." + old.rsplit(".", 1)[1]
            with self.subTest(root=root):
                try:
                    legacy = importlib.import_module(root)
                except ModuleNotFoundError as error:
                    if error.name == root:
                        continue
                    raise
                self.assertIs(legacy, importlib.import_module(new))

    def test_unimplemented_modules_remain_unimplemented(self):
        root = Path(atlaspm.__file__).resolve().parent
        for dest in ("strategies/options", "strategies/bonds", "hedging"):
            self.assertEqual(list((root / dest).glob("*.py")), [])
        self.assertFalse((root / "portfolio" / "__init__.py").exists())

if __name__ == "__main__":
    unittest.main()
