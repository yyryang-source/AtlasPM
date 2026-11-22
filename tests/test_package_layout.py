"""Regression tests for AtlasPM's two-domain package layout and legacy imports."""

from __future__ import annotations

import importlib
from pathlib import Path
import unittest

import atlaspm


class PackageLayoutTest(unittest.TestCase):
    def test_quant_and_selection_are_physically_distinct(self):
        pkg = Path(atlaspm.__file__).resolve().parent
        self.assertTrue((pkg / "quant" / "atlas_fusionnet.py").is_file())
        self.assertTrue((pkg / "quant" / "ibkr_gateway.py").is_file())
        self.assertTrue((pkg / "quant" / "portfolio.py").is_file())
        self.assertTrue((pkg / "selection" / "ai_stock_ranker_v2.py").is_file())
        self.assertTrue((pkg / "selection" / "alpha_eligibility.py").is_file())
        self.assertTrue((pkg / "selection" / "monthly_stock_selection.py").is_file())
        self.assertTrue((pkg / "cli.py").is_file())

    def test_legacy_imports_point_to_exact_same_module(self):
        mapping = {
            "ai_stock_ranker": "selection",
            "ai_stock_ranker_v2": "selection",
            "monthly_stock_selection": "selection",
            "alpha_eligibility": "selection",
            "us_market_universe": "selection",
            "stock_screener": "selection",
            "atlas_risknet": "quant",
            "atlas_fusionnet": "quant",
            "portfolio": "quant",
            "ibkr_gateway": "quant",
            "risk_budget": "quant",
            "ml_lightgbm": "quant",
        }
        for module, domain in mapping.items():
            with self.subTest(module=module):
                compatibility = importlib.import_module("atlaspm." + module)
                canonical = importlib.import_module("atlaspm." + domain + "." + module)
                self.assertIs(compatibility, canonical)

    def test_legacy_cli_remains_accessible(self):
        module = importlib.import_module("atlaspm.cli")
        self.assertTrue(callable(module.main))
        self.assertTrue(callable(module.build_parser))


if __name__ == "__main__":
    unittest.main()
