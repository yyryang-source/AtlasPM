"""Stage 4 compatibility checks for numeric and JSON-object validation.

No broker calls, live permissions, or downloaded market data are used.
"""
from __future__ import annotations

from collections import UserDict
import importlib
import math
import re
import unittest

NUMERIC = (
    ("engine.contracts", "finite", "field: finite number required", None, True),
    ("execution.broker_reconcile", "_num", "field must be a finite number", None, True),
    ("execution.ibkr_gateway", "_num", "field must be a finite number", None, True),
    ("execution.ibkr_mapping", "_finite", "field must be finite numeric data", None, True),
    ("execution.institutional_batch", "_num", "field must be a finite number", None, False),
    ("execution.institutional_gate", "_number", "field must be a finite number", None, False),
    ("execution.institutional_rebalance", "_num", "field must be a finite number", None, False),
    ("execution.paper_oms", "_number", "field must be finite and numeric", None, False),
    ("monitoring.audit", "_number", "field must be a finite number", "field must be finite", True),
    ("monitoring.ibkr_risk_chain_audit", "_number", "field must be finite numeric data", None, True),
    ("research.atlas_robustness_audit", "_number", "Invalid nonfinite metric field", None, True),
    ("risk.ibkr_margin_monitor", "_number", "field must be a finite JSON number", None, True),
    ("risk.ibkr_margin_stress", "_number", "field must be finite numeric data", None, True),
    ("risk.ibkr_multiasset_budget", "_num", "field must be a finite number", None, True),
    ("risk.ibkr_reverse_stress", "_number", "field requires finite numeric data", None, True),
    ("risk.risk_budget", "_finite", "field must be a finite number", "field outside permitted range", False),
)
OBJECTS = (
    ("engine.contracts", "object_map", ": JSON object required"),
    ("execution.broker_reconcile", "_obj", " must be a JSON object"),
    ("execution.ibkr_mapping", "_obj", " must be an object"),
    ("execution.institutional_batch", "_obj", " must be a JSON object"),
    ("execution.institutional_gate", "_object", " must be a JSON object"),
    ("execution.institutional_ledger", "_object", " must be a JSON object"),
    ("execution.institutional_rebalance", "_obj", " must be a JSON object"),
    ("monitoring.ibkr_dashboard", "_object", " must be an object"),
    ("monitoring.ibkr_risk_chain_audit", "_object", " must be a JSON object"),
    ("risk.ibkr_financing", "_object", " must be an object"),
    ("risk.ibkr_margin_stress", "_obj", " must be a JSON object"),
    ("risk.ibkr_multiasset_budget", "_obj", " must be an object"),
    ("risk.ibkr_reverse_stress", "_object", " must be an object"),
)


def invoke(module, name, value, **kwargs):
    fn = getattr(importlib.import_module("atlaspm." + module), name)
    if module == "execution.institutional_gate":
        return fn({"field": value}, "field", **kwargs)
    return fn(value, "field", **kwargs)


class Stage4ValidationTests(unittest.TestCase):
    def test_16_numeric_default_policies_and_exact_errors(self):
        self.assertEqual(len(NUMERIC), 16)
        for module, name, wrong_type, nonfinite, negative in NUMERIC:
            with self.subTest(module=module):
                for v in (True, None, "5", {}, []):
                    with self.assertRaisesRegex(ValueError, "^" + re.escape(wrong_type) + "$"):
                        invoke(module, name, v)
                for v in (float("nan"), float("inf"), float("-inf")):
                    message = nonfinite if nonfinite is not None else wrong_type
                    with self.assertRaisesRegex(ValueError, "^" + re.escape(message) + "$"):
                        invoke(module, name, v)
                self.assertEqual(math.copysign(1, invoke(module, name, -0.0)), -1)
                self.assertEqual(invoke(module, name, 0), 0)
                if negative:
                    self.assertEqual(invoke(module, name, -1), -1)
                else:
                    with self.assertRaises(ValueError):
                        invoke(module, name, -1)

    def test_opt_in_strict_and_bounds_are_unchanged(self):
        strict = (
            ("execution.institutional_batch", "_num", {"positive": True}, "field must be positive"),
            ("execution.paper_oms", "_number", {"positive": True}, "field must be positive"),
            ("execution.institutional_rebalance", "_num", {"allow_zero": False}, "field must be positive"),
            ("risk.risk_budget", "_finite", {"strict": True}, "field outside permitted range"),
        )
        for module, name, kwargs, msg in strict:
            with self.subTest(module=module):
                with self.assertRaisesRegex(ValueError, "^" + re.escape(msg) + "$"):
                    invoke(module, name, 0, **kwargs)
        bounds = (
            ("engine.contracts", "finite", -1, {"minimum": 0}, "field: must be >= 0"),
            ("execution.institutional_gate", "_number", 11, {"lo": 0, "maxval": 10}, "field is outside the valid range"),
            ("monitoring.ibkr_risk_chain_audit", "_number", -1, {"low": 0}, "field below allowed range"),
            ("risk.ibkr_margin_stress", "_number", 11, {"maximum": 10}, "field exceeds the allowed range"),
            ("risk.ibkr_multiasset_budget", "_num", -1, {"low": 0}, "field below allowed range"),
            ("risk.ibkr_reverse_stress", "_number", 11, {"maximum": 10}, "field above range"),
        )
        for module, name, value, kwargs, msg in bounds:
            with self.subTest(module=module):
                with self.assertRaisesRegex(ValueError, "^" + re.escape(msg) + "$"):
                    invoke(module, name, value, **kwargs)
        self.assertEqual(invoke("execution.institutional_gate", "_number", -5, lo=-math.inf), -5)

    def test_13_object_checks_retain_dict_identity_and_text(self):
        self.assertEqual(len(OBJECTS), 13)
        for module, name, suffix in OBJECTS:
            fn = getattr(importlib.import_module("atlaspm." + module), name)
            with self.subTest(module=module):
                original = {"a": 1}
                self.assertIs(fn(original, "field"), original)
                for v in (UserDict(a=1), None, True, [], 1):
                    with self.assertRaisesRegex(ValueError, "^field" + re.escape(suffix) + "$"):
                        fn(v, "field")


if __name__ == "__main__":
    unittest.main()
