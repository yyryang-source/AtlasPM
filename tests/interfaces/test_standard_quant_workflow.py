"""Standard quant workflow registry: topology, capability honesty, safety."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import unittest

from atlaspm.framework import (
    ASSET_SLEEVES, CROSS_CUTTING_CONTROLS, REQUIRED_GATES, STAGES,
    AssetSleeve, WorkflowStage, mermaid_flowchart, validate_workflow,
    workflow_report,
)
from atlaspm.framework.__main__ import main


class StandardWorkflowContractTests(unittest.TestCase):
    def test_eight_ordered_steps_and_three_distinct_asset_sleeves(self):
        validate_workflow()
        self.assertEqual(
            tuple(step.code for step in STAGES),
            ("data", "alpha", "validation", "sleeves", "portfolio",
             "pretrade", "execution", "monitor"),
        )
        self.assertEqual(
            {sleeve.code for sleeve in ASSET_SLEEVES},
            {"equity", "options", "bonds"},
        )
        self.assertEqual(STAGES[0].depends_on, ())
        for previous, current in zip(STAGES, STAGES[1:]):
            self.assertEqual(current.depends_on, (previous.code,))

    def test_all_existing_research_module_paths_are_real_and_import_resolvable(self):
        # Do not load model weights, optional data dependencies or brokers.
        paths = {
            path
            for item in (*STAGES, *ASSET_SLEEVES)
            for path in item.existing_modules
        }
        self.assertGreaterEqual(len(paths), 13)
        for module in paths:
            with self.subTest(module=module):
                self.assertIsNotNone(importlib.util.find_spec(module))

    def test_unknown_out_of_order_cycles_and_duplicate_assets_are_rejected(self):
        a = WorkflowStage("data", "Data", "Ingest", "research")
        b = WorkflowStage("alpha", "Alpha", "Research", "research", ("data",))
        for stages in (
            (a, a),
            (b, a),
            (WorkflowStage("cyclic", "Loop", "Invalid", "planned", ("cyclic",)),),
            (WorkflowStage("missing", "Missing", "Invalid", "planned", ("z",)),),
            (WorkflowStage("bad", "Bad", "Invalid", "not_a_status"),),
        ):
            with self.subTest(stages=stages), self.assertRaises(ValueError):
                validate_workflow(stages, ASSET_SLEEVES)
        with self.assertRaisesRegex(ValueError, "Duplicate or invalid asset sleeve"):
            validate_workflow((a, b), ASSET_SLEEVES + (ASSET_SLEEVES[0],))

    def test_architecture_does_not_claim_execution_or_alpha_approval(self):
        report = workflow_report()
        self.assertEqual(report["schema"], "atlaspm_standard_quant_workflow_v1")
        for flag in (
            "architecture_only", "research_only",
        ):
            self.assertTrue(report[flag])
        for flag in (
            "live_execution_enabled", "automatic_broker_orders_enabled",
            "automatic_stock_to_portfolio_handoff_enabled",
            "hedging_engine_implemented", "stock_alpha_validated_for_execution",
        ):
            self.assertIs(report[flag], False)
        sleeves = {s["code"]: s for s in report["asset_sleeves"]}
        self.assertEqual(sleeves["options"]["capability"], "planned")
        self.assertEqual(sleeves["bonds"]["capability"], "planned")
        self.assertTrue(sleeves["equity"]["existing_modules"])
        self.assertGreaterEqual(len(REQUIRED_GATES), 4)
        self.assertGreaterEqual(len(CROSS_CUTTING_CONTROLS), 5)
        self.assertEqual(
            report["feedback_loop"],
            {"from": "monitor", "to": "alpha", "type": "research_review_only"},
        )

    def test_mermaid_has_parallel_asset_sleeves_and_cross_cutting_risk(self):
        diagram = mermaid_flowchart()
        self.assertTrue(diagram.startswith("flowchart LR\n"))
        for edge in (
            "data --> alpha", "alpha --> validation",
            "sleeves --> equity", "sleeves --> options",
            "sleeves --> bonds", "equity --> portfolio",
            "options --> portfolio", "bonds --> portfolio",
            "portfolio --> pretrade", "pretrade --> execution",
            "execution --> monitor", "controls -.-> pretrade",
            "monitor -.->|复盘与重新验证| alpha",
        ):
            with self.subTest(edge=edge):
                self.assertIn(edge, diagram)

    def test_module_cli_is_inspect_only_and_outputs_clean_json_or_mermaid(self):
        for style in ("json", "mermaid"):
            with self.subTest(style=style):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(main(["--format", style]), 0)
                printed = stdout.getvalue()
                if style == "json":
                    payload = json.loads(printed)
                    self.assertFalse(payload["live_execution_enabled"])
                    self.assertEqual(len(payload["stages"]), 8)
                else:
                    self.assertIn("flowchart LR", printed)


if __name__ == "__main__":
    unittest.main()
