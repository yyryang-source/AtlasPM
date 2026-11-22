"""Research-only monthly AI alpha gate tests: no portfolio orders or broker calls."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from atlaspm.alpha_eligibility import (
    GATE_SCHEMA, build_alpha_eligibility, build_from_file,
    research_action_check, write_eligibility_json,
)
from atlaspm.monthly_stock_selection import SCHEMA as MONTHLY_SCHEMA

REPORT_SHA = "a" * 64
MODEL_SHA = "b" * 64


def example_monthly_research():
    candidates = [
        {
            "symbol": f"STK{i:03d}",
            "rank": i,
            "display_position": i,
            "ranking_score": round((51 - i) / 100, 7),
            "tie_count": 1,
        }
        for i in range(1, 51)
    ]
    return {
        "schema": MONTHLY_SCHEMA,
        "month": "2026-10",
        "next_calendar_month": "2026-11",
        "as_of_nyse_session": "2026-10-30",
        "research_only": True,
        "automated_handoff_approved": False,
        "live_execution_enabled": False,
        "order_instructions": [],
        "report_status": "READY_FOR_RESEARCH_REVIEW",
        "review_reasons": [],
        "model": {
            "sha256": MODEL_SHA,
            "score_semantics": "ordinal_relative_rank_utility_not_return",
            "training_age_days": 60,
        },
        "data_quality": {
            "scored_at_month_end": 1000,
            "listing_manifest_count": 1040,
            "listed_market_coverage_pct": 96.15,
        },
        "top_20": {
            "requested": 20, "returned": 20,
            "boundary_tied": False, "candidates": candidates[:20],
        },
        "top_50": {
            "requested": 50, "returned": 50, "complete": True,
            "boundary_tied": False, "candidates": candidates,
        },
    }


class AlphaEligibilityGateTest(unittest.TestCase):
    def setUp(self):
        self.monthly = example_monthly_research()

    def _gate(self, **kwargs):
        return build_alpha_eligibility(
            self.monthly, monthly_report_sha256=REPORT_SHA, **kwargs
        )

    def test_top_percent_and_absolute_top50_cap(self):
        gate = self._gate()
        self.assertEqual(gate["schema"], GATE_SCHEMA)
        self.assertEqual(gate["research_eligibility_status"], "RESEARCH_CANDIDATES_AVAILABLE")
        self.assertEqual(gate["eligibility_rule"]["requested_maximum_eligible"], 50)
        self.assertEqual(gate["eligible_count"], 50)
        self.assertEqual(gate["valid_for_month"], "2026-11")
        self.assertEqual(gate["eligible_symbols"][0], "STK001")
        self.assertEqual(gate["eligible_symbols"][-1], "STK050")
        self.assertFalse(gate["live_execution_enabled"])
        self.assertFalse(gate["automated_handoff_approved"])
        self.assertEqual(gate["order_instructions"], [])
        small = self._gate(top_percent=2)
        self.assertEqual(small["eligible_count"], 20)
        smaller = self._gate(top_percent=2, max_ranked_names=8)
        self.assertEqual(smaller["eligible_count"], 8)
        self.assertTrue(all(
            item["model_score_is_return_forecast"] is False
            for item in gate["new_or_increase_research_candidates"]
        ))

    def test_block_upstream_review_stale_coverage_and_small_universe(self):
        for case in ("upstream", "stale", "coverage", "small"):
            with self.subTest(case=case):
                self.monthly = example_monthly_research()
                if case == "upstream":
                    self.monthly["report_status"] = "REVIEW_REQUIRED"
                    self.monthly["review_reasons"] = ["STALE_MODEL: 2860 days"]
                if case == "stale":
                    self.monthly["model"]["training_age_days"] = 2860
                if case == "coverage":
                    self.monthly["data_quality"]["listed_market_coverage_pct"] = None
                if case == "small":
                    self.monthly["data_quality"]["scored_at_month_end"] = 38
                gate = self._gate()
                self.assertEqual(gate["eligible_symbols"], [])
                self.assertEqual(gate["eligible_count"], 0)
                self.assertEqual(gate["research_eligibility_status"], "BLOCKED_DATA_OR_MODEL")
                self.assertTrue(gate["blocked_reasons"])

    def test_threshold_splits_equal_score_group_blocks_all_candidates(self):
        # Target Top30; ranks 30 & 31 are true ties.
        self.monthly["top_50"]["candidates"][29]["tie_count"] = 2
        self.monthly["top_50"]["candidates"][30]["rank"] = 30
        self.monthly["top_50"]["candidates"][30]["tie_count"] = 2
        self.monthly["top_50"]["candidates"][30]["ranking_score"] = (
            self.monthly["top_50"]["candidates"][29]["ranking_score"]
        )
        gate = self._gate(top_percent=3)
        self.assertEqual(gate["eligible_count"], 0)
        self.assertTrue(any("AMBIGUOUS_THRESHOLD_BOUNDARY" in x
                            for x in gate["blocked_reasons"]))

    def test_research_open_increase_holds_and_protective_exits(self):
        gate = self._gate(top_percent=2)
        for action in ("OPEN", "INCREASE"):
            check = research_action_check(
                gate, symbol="stk003", action=action, decision_date="2026-11-02"
            )
            self.assertEqual(check["result"], "ELIGIBLE_FOR_SEPARATE_QUANT_REVIEW")
            self.assertTrue(check["alpha_research_candidate"])
            self.assertFalse(check["actual_trade_approval"])
            denied = research_action_check(
                gate, symbol="STK035", action=action, decision_date="2026-11-02"
            )
            self.assertEqual(denied["result"], "DENY_NOT_IN_ELIGIBLE_UNIVERSE")
            self.assertFalse(denied["alpha_research_candidate"])
        stale = research_action_check(
            gate, symbol="STK003", action="OPEN", decision_date="2026-12-01"
        )
        self.assertEqual(stale["result"], "DENY_OUTDATED_MONTHLY_ALPHA")
        self.assertFalse(stale["alpha_research_candidate"])
        for action in ("REDUCE", "CLOSE"):
            result = research_action_check(
                gate, symbol="STK035", action=action, decision_date="2026-12-01"
            )
            self.assertTrue(result["alpha_research_candidate"])
            self.assertFalse(result["actual_trade_approval"])
            self.assertEqual(result["result"], "NOT_RESTRICTED_BY_ALPHA_GATE")
        hold = research_action_check(
            gate, symbol="STK035", action="HOLD", decision_date="2026-12-01"
        )
        self.assertEqual(hold["result"], "EXISTING_POSITION_NO_ALPHA_ENTRY_CHECK")

    def test_invalid_inputs_cannot_bypass_research_guard(self):
        for kwargs in (
            {"top_percent": 0}, {"top_percent": float("nan")},
            {"top_percent": float("inf")}, {"max_ranked_names": 51},
            {"minimum_scored_names": 0}, {"maximum_model_age_days": -1},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self._gate(**kwargs)
        fake = copy.deepcopy(self.monthly)
        fake["automated_handoff_approved"] = True
        with self.assertRaisesRegex(ValueError, "Unexpected automated"):
            build_alpha_eligibility(fake, monthly_report_sha256=REPORT_SHA)
        fake = copy.deepcopy(self.monthly)
        fake["model"]["score_semantics"] = "predicted_return"
        with self.assertRaisesRegex(ValueError, "ordinal"):
            build_alpha_eligibility(fake, monthly_report_sha256=REPORT_SHA)
        with self.assertRaisesRegex(ValueError, "Unknown action"):
            research_action_check(self._gate(), symbol="STK003", action="BUY", decision_date="2026-11-01")

    def test_actual_38_stock_monthly_result_must_have_empty_allowlist(self):
        report = copy.deepcopy(self.monthly)
        report["report_status"] = "REVIEW_REQUIRED"
        report["review_reasons"] = [
            "STALE_MODEL: trained through 2018-12-31, 2860 days before month-end",
            "UNVERIFIED_FULL_US_MARKET_SCOPE",
            "TOP50_INCOMPLETE",
        ]
        report["data_quality"]["scored_at_month_end"] = 38
        report["data_quality"]["listed_market_coverage_pct"] = None
        report["model"]["training_age_days"] = 2860
        report["top_50"]["candidates"] = report["top_50"]["candidates"][:38]
        report["top_50"]["returned"] = 38
        result = build_alpha_eligibility(report, monthly_report_sha256=REPORT_SHA)
        self.assertEqual(result["eligible_symbols"], [])
        self.assertEqual(result["research_eligibility_status"], "BLOCKED_DATA_OR_MODEL")
        self.assertTrue(result["reduce_and_close_remain_outside_alpha_filter"])

    def test_cli_private_path_json_and_safety(self):
        from atlaspm.cli import main
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        old_cwd = Path.cwd()
        try:
            os.chdir(root)
            folder = root / "local_data"
            folder.mkdir()
            monthly_path = folder / "monthly.json"
            gate_path = folder / "gate.json"
            monthly_path.write_text(json.dumps(self.monthly), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                invalid = main([
                    "stock-alpha-gate", "--monthly-report", str(monthly_path),
                    "--output", str(root / "public_gate.json"),
                ])
            self.assertEqual(invalid, 2)
            self.assertFalse((root / "public_gate.json").exists())
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                ok = main([
                    "stock-alpha-gate", "--monthly-report", str(monthly_path),
                    "--output", str(gate_path),
                ])
            self.assertEqual(ok, 0)
            self.assertEqual(json.loads(stdout.getvalue())["eligible_count"], 50)
            self.assertTrue(gate_path.exists())
            on_disk = json.loads(gate_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["source_report_status"], "READY_FOR_RESEARCH_REVIEW")
            check_text = io.StringIO()
            with contextlib.redirect_stdout(check_text):
                status = main([
                    "stock-alpha-check", "--gate", str(gate_path),
                    "--symbol", "STK002", "--action", "OPEN",
                    "--decision-date", "2026-11-12",
                ])
            self.assertEqual(status, 0)
            self.assertFalse(json.loads(check_text.getvalue())["actual_trade_approval"])
            with contextlib.redirect_stderr(io.StringIO()):
                conflict = main([
                    "stock-alpha-gate", "--monthly-report", str(monthly_path),
                    "--output", str(gate_path),
                ])
            self.assertEqual(conflict, 2)
            self.assertEqual(
                build_from_file(monthly_path)["monthly_report_sha256"],
                on_disk["monthly_report_sha256"],
            )
            with self.assertRaisesRegex(ValueError, "use --overwrite"):
                write_eligibility_json(on_disk, gate_path)
        finally:
            os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
