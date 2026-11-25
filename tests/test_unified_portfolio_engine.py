"""Offline Unified Portfolio Engine V1: real OMS adapters and negative contracts."""
from __future__ import annotations

import copy
import json
import importlib.util
import unittest
from pathlib import Path

from atlaspm.cli import main
from atlaspm.engine.unified import run_unified_portfolio


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("scipy"), "Optional optimizer extra needed: pip install -e .[portfolio]")
class UnifiedPortfolioEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(
            (ROOT / "examples" / "unified_portfolio_synthetic.json").read_text(encoding="utf-8")
        )

    def run_case(self, payload: dict | None = None) -> dict:
        return run_unified_portfolio(self.payload if payload is None else payload)

    def test_full_pipeline_uses_actual_oms_and_reconciles(self) -> None:
        r = self.run_case()
        self.assertEqual(r["decision"], "SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE", r.get("block_reasons"))
        self.assertTrue(r["research_only"])
        self.assertFalse(r["live_execution_enabled"])
        self.assertFalse(r["broker_order_submission_enabled"])
        self.assertFalse(r["approved_for_model_training"])
        self.assertEqual(r["signal_count"], 2)
        self.assertEqual(len(r["generated_order_intents"]), 2)
        self.assertEqual(r["institutional_preflight"]["decision"], "ALLOW_PAPER_INTENT")
        self.assertEqual(r["paper_oms"]["decision"], "PAPER_REPLAY_COMPLETED")
        self.assertTrue(all(r["reconciliation_checks"].values()))
        self.assertEqual([o["status"] for o in r["paper_oms"]["orders"]], ["FILLED", "FILLED"])
        self.assertAlmostEqual(r["paper_oms"]["account_replay"]["settled_cash"], 5998)
        self.assertAlmostEqual(r["paper_oms"]["account_replay"]["marked_net_liquidation"], 29998)
        self.assertIsNotNone(r["analytics"]["performance"]["portfolio"]["cagr"])
        self.assertIn("SPY", r["analytics"]["performance"]["benchmarks"])

    def test_partial_fill_and_cancellation_reconcile_partial_quantity(self) -> None:
        self.payload["paper"]["fill_fraction"] = 0.5
        r = self.run_case()
        self.assertEqual(r["decision"], "SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE", r.get("block_reasons"))
        self.assertEqual([o["status"] for o in r["paper_oms"]["orders"]], ["CANCELED"]*2)
        self.assertEqual([o["filled_quantity"] for o in r["paper_oms"]["orders"]], [10,10])
        self.assertTrue(all(r["reconciliation_checks"].values()))

    def test_duplicate_identical_journal_event_does_not_duplicate_fill(self) -> None:
        self.payload["paper"]["duplicate_last_event"] = True
        r = self.run_case()
        self.assertEqual(r["decision"], "SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE", r.get("block_reasons"))
        self.assertEqual(r["paper_oms"]["duplicate_events_ignored"], 1)
        self.assertAlmostEqual(r["paper_oms"]["account_replay"]["settled_cash"], 5998)

    def test_all_optimizer_interfaces_are_explicit_and_fail_closed(self) -> None:
        for method in ("equal_weight", "risk_parity", "minimum_variance",
                       "mean_variance", "cvar", "maximum_diversification",
                       "risk_budgeting"):
            with self.subTest(method=method):
                payload = copy.deepcopy(self.payload)
                payload["constraints"]["method"] = method
                if method == "risk_budgeting":
                    payload["constraints"]["risk_budgets"] = {"AAA":.5,"BBB":.5}
                out = self.run_case(payload)
                self.assertIn(out["decision"], {
                    "BLOCK", "NO_REBALANCE_NEEDED", "SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE"
                })
                # No silent optimizer fallback: an unfeasible discrete trade
                # is a BLOCK, never a bypass to equal weight.
                if out["decision"] != "BLOCK":
                    self.assertEqual(out["allocation"]["method"], method)
                else:
                    self.assertNotEqual(out["block_reasons"], [])

    def test_expired_future_and_stale_signals_block(self) -> None:
        cases = [
            ("expires_at","2026-11-20T20:59:59Z","LOOKAHEAD_OR_EXPIRED_SIGNAL"),
            ("available_at","2026-11-20T21:01:00Z","LOOKAHEAD_OR_EXPIRED_SIGNAL"),
            ("data_available_at","2026-11-20T21:01:00Z","LOOKAHEAD_OR_EXPIRED_SIGNAL"),
            ("data_source","unverified_feed","DATA_PROVENANCE_UNVERIFIED"),
            ("security_id","UNVERIFIED","SECURITY_IDENTITY_UNVERIFIED"),
        ]
        for key,value,reason in cases:
            with self.subTest(key=key):
                p = copy.deepcopy(self.payload)
                p["signals"][0][key] = value
                result = self.run_case(p)
                self.assertEqual(result["decision"],"BLOCK")
                self.assertIn(reason, result["block_reasons"][0])
                self.assertTrue(result["independent_reduction_research"])

    def test_model_status_cannot_be_self_promoted(self) -> None:
        self.payload["signals"][0]["model_approved_for_training"] = True
        out = self.run_case()
        self.assertEqual(out["decision"],"BLOCK")
        self.assertIn("MODEL_PERMISSION_CLAIM_NOT_SUPPORTED",out["block_reasons"][0])

    def test_live_permission_or_real_mode_cannot_be_enabled(self) -> None:
        for key,val in (("live_execution_enabled",True),
                        ("broker_order_submission_enabled",True),
                        ("research_only",False),("mode","real")):
            with self.subTest(key=key):
                p=copy.deepcopy(self.payload); p[key]=val
                self.assertEqual(self.run_case(p)["decision"],"BLOCK")

    def test_missing_price_and_unheld_security_fail_before_oms(self) -> None:
        p = copy.deepcopy(self.payload)
        del p["account"]["positions"][0]["mark_price"]
        self.assertEqual(self.run_case(p)["decision"], "BLOCK")
        p=copy.deepcopy(self.payload)
        p["account"]["positions"] = p["account"]["positions"][:1]
        self.assertIn("OMS_UNHELD_EQUITY_UNSUPPORTED",self.run_case(p)["block_reasons"][0])

    def test_conflicting_sector_and_name_caps_rejected(self) -> None:
        p=copy.deepcopy(self.payload)
        p["constraints"]["sector_limits"]={"Technology":.1,"Healthcare":.1}
        self.assertIn("INFEASIBLE_SECTOR_CAPS",self.run_case(p)["block_reasons"][0])
        p=copy.deepcopy(self.payload)
        p["constraints"]["max_name_weight"]=.1
        self.assertIn("INFEASIBLE_NAME_CAP",self.run_case(p)["block_reasons"][0])

    def test_margin_requirements_unverified_block_additions_but_not_sell_research(self) -> None:
        p=copy.deepcopy(self.payload)
        p["account"]["account_type"]="margin"
        p["account"]["margin_debt"]=500.
        out=self.run_case(p)
        self.assertEqual(out["decision"],"BLOCK")
        self.assertIn("MARGIN_PER_SECURITY_REQUIREMENTS_UNKNOWN",out["block_reasons"])
        self.assertEqual(len(out["independent_reduction_research"]),2)

    def test_reconciliation_failure_is_not_hidden(self) -> None:
        self.payload["account"]["broker_reported_net_liquidation"]=29999.
        out=self.run_case()
        self.assertEqual(out["decision"],"BLOCK")
        self.assertIn("ACCOUNT_LEDGER_RECONCILIATION_FAILURE",out["block_reasons"][0])

    def test_cash_not_funded_by_unsettled_sales(self) -> None:
        p=copy.deepcopy(self.payload)
        p["account"]["settled_cash"]=100.
        # A near-zero starting cash account cannot rebalance from proceeds
        # of an unsettled sale. Never add an invented same-day credit.
        result=self.run_case(p)
        self.assertEqual(result["decision"],"BLOCK")

    def test_risk_shock_exceeds_budget(self) -> None:
        self.payload["constraints"]["max_stress_loss_fraction"]=0.1
        out=self.run_case()
        self.assertEqual(out["decision"],"BLOCK")
        self.assertIn("STRESS_LOSS_BUDGET_EXCEEDED",out["block_reasons"])

    def test_paper_reject_preserves_starting_holdings(self) -> None:
        self.payload["paper"]["fill_fraction"]=0
        out=self.run_case()
        self.assertEqual(out["decision"],"SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE",out.get("block_reasons"))
        self.assertEqual([o["status"] for o in out["paper_oms"]["orders"]], ["REJECTED","REJECTED"])
        self.assertEqual(out["paper_oms"]["account_replay"]["settled_cash"],10000)

    def test_cvar_sparse_constraints_and_large_scenario_set(self) -> None:
        from unittest.mock import patch
        from scipy.optimize import linprog
        from scipy.sparse import issparse
        from atlaspm.engine.allocation import optimize_target
        from atlaspm.engine.contracts import Account, validate_candidates, timestamp
        account=Account.from_payload(self.payload["account"])
        candidates=validate_candidates(self.payload["signals"],
                    as_of=timestamp(self.payload["as_of"],"as_of"),max_age_seconds=86400)
        market=copy.deepcopy(self.payload["market"])
        market["scenario_returns"]=market["scenario_returns"]*100
        captured=[]
        def checked(*args,**kwargs):
            matrix=kwargs["A_ub"]
            self.assertTrue(issparse(matrix))
            self.assertTrue(issparse(kwargs["A_eq"]))
            self.assertLess(matrix.nnz,matrix.shape[0]*matrix.shape[1]//10)
            captured.append(matrix.shape)
            return linprog(*args,**kwargs)
        with patch("scipy.optimize.linprog",side_effect=checked):
            result=optimize_target(candidates,account,market,dict(self.payload["constraints"],method="cvar"))
        self.assertTrue(captured)
        self.assertAlmostEqual(sum(result["weights"].values()),.805,delta=1e-6)
        self.assertFalse(result["live_execution_enabled"])

    def test_cash_slack_does_not_force_negative_return_exposure(self) -> None:
        from atlaspm.engine.allocation import optimize_target
        from atlaspm.engine.contracts import Account, validate_candidates, timestamp
        account=Account.from_payload(self.payload["account"])
        candidates=validate_candidates(self.payload["signals"],
                    as_of=timestamp(self.payload["as_of"],"as_of"),max_age_seconds=86400)
        market=copy.deepcopy(self.payload["market"])
        market["expected_returns"]={"AAA":-.2,"BBB":-.1}
        constraints=dict(self.payload["constraints"],method="mean_variance",allow_cash_slack=True)
        result=optimize_target(candidates,account,market,constraints)
        self.assertAlmostEqual(result["target_cash_fraction"],1.,places=6)
        self.assertFalse(result["broker_order_submission_enabled"])
        market["expected_returns"]={"AAA":.3,"BBB":.2}
        constraints.update(max_annual_volatility=.03,max_stress_loss_fraction=.02)
        result=optimize_target(candidates,account,market,constraints)
        self.assertLessEqual(sum(result["weights"].values()),.1+1e-6)
        self.assertLessEqual(result["modeled_annual_volatility"],.03+1e-6)
        self.assertGreater(sum(result["weights"].values()),0.)
        self.assertAlmostEqual(result["target_cash_fraction"],1-sum(result["weights"].values()))
        self.assertTrue(result["risk_constraints_in_solver"])
        with self.assertRaises(ValueError):
            optimize_target(candidates,account,market,dict(constraints,risk_aversion=-1.))

    def test_cash_slack_uses_lower_return_and_settled_buy_budget(self) -> None:
        from atlaspm.engine.allocation import optimize_target
        from atlaspm.engine.contracts import Account, validate_candidates, timestamp
        account=Account.from_payload(self.payload["account"])
        candidates=validate_candidates(self.payload["signals"],
                    as_of=timestamp(self.payload["as_of"],"as_of"),max_age_seconds=86400)
        market=copy.deepcopy(self.payload["market"])
        market["expected_returns"]={"AAA":.9,"BBB":.8}
        constraints=dict(self.payload["constraints"],method="mean_variance",allow_cash_slack=True)
        result=optimize_target(candidates,account,market,constraints,
                               paper={"fee_per_order":5.,"slippage_bps":10.})
        limit=(10000.-.19*30000.-10.)/30000.
        self.assertAlmostEqual(result["settled_buy_budget_fraction"],limit)
        self.assertLessEqual(result["modeled_buy_cash_fraction"],limit+1e-6)
        self.assertFalse(result["sale_proceeds_used_for_buys"])
        market["expected_return_lower_bounds"]={"AAA":-.2,"BBB":-.1}
        result=optimize_target(candidates,account,market,constraints)
        self.assertTrue(result["lower_return_objective_used"])
        self.assertAlmostEqual(result["target_cash_fraction"],1.,places=6)
        self.assertFalse(result["lower_bounds_independently_calibrated"])
        self.assertFalse(result["live_execution_enabled"])
        for bad in ({"AAA":0.},{"AAA":1.,"BBB":0.},{"AAA":float("nan"),"BBB":0.}):
            market["expected_return_lower_bounds"]=bad
            with self.assertRaises(ValueError):
                optimize_target(candidates,account,market,constraints)

    def test_cash_slack_keeps_independent_risk_and_synthetic_permissions(self) -> None:
        self.payload["market"]["expected_returns"]={"AAA":-.2,"BBB":-.1}
        self.payload["constraints"].update(method="mean_variance",allow_cash_slack=True)
        result=self.run_case()
        self.assertIn(result["decision"],{"BLOCK","SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE"})
        self.assertFalse(result["live_execution_enabled"])
        self.assertFalse(result["broker_order_submission_enabled"])
        if "allocation" in result:
            self.assertAlmostEqual(result["allocation"]["target_cash_fraction"],1.,places=6)
        self.payload["constraints"]["method"]="equal_weight"
        result=self.run_case()
        self.assertEqual(result["decision"],"BLOCK")
        self.assertIn("CASH_SLACK_REQUIRES_SYNTHETIC_CASH_MEAN_VARIANCE",result["block_reasons"][0])

    def test_direct_optimizer_methods_obey_all_fractional_limits(self) -> None:
        from atlaspm.engine.allocation import optimize_target
        from atlaspm.engine.contracts import Account, validate_candidates, timestamp
        as_of = timestamp(self.payload["as_of"], "as_of")
        account = Account.from_payload(self.payload["account"])
        candidates = validate_candidates(self.payload["signals"], as_of=as_of,
                                         max_age_seconds=86400)
        for method in ("equal_weight", "risk_parity", "minimum_variance", "mean_variance",
                       "cvar", "maximum_diversification", "risk_budgeting"):
            with self.subTest(method=method):
                constraints = dict(self.payload["constraints"], method=method)
                if method == "risk_budgeting":
                    constraints["risk_budgets"] = {"AAA": .5, "BBB": .5}
                result = optimize_target(candidates, account, self.payload["market"], constraints)
                self.assertEqual(result["method"], method)
                weights = result["weights"]
                self.assertAlmostEqual(sum(weights.values()), .805, delta=1e-6)
                self.assertTrue(all(0 <= x <= .55+1e-6 for x in weights.values()))
                self.assertLessEqual(result["turnover_fraction_conservative"], 1+1e-6)

    def test_real_oms_sale_creates_unsettled_receivable(self) -> None:
        self.payload["constraints"].update({
            "method": "minimum_variance",
            "min_cash_fraction": .05,
            "max_gross_exposure": .785,
            "max_name_weight": .555,
        })
        result = self.run_case()
        self.assertEqual(result["decision"], "SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE",
                         result.get("block_reasons"))
        self.assertTrue(any(leg["action"] == "SELL_EQUITY"
                            for leg in result["generated_order_intents"]))
        self.assertGreater(result["paper_oms"]["account_replay"]["unsettled_receivable"], 0)
        self.assertTrue(result["reconciliation_checks"]["unsettled_sale_receivables"])

    def test_synthetic_market_impact_is_reported_separately(self) -> None:
        self.payload["paper"]["market_impact_bps"] = 2
        result = self.run_case()
        self.assertEqual(result["decision"], "SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE",
                         result.get("block_reasons"))
        self.assertGreater(result["analytics"]["paper_market_impact_assumption_usd"], 0)
        self.assertEqual(result["analytics"]["paper_slippage_usd"], 0)

    def test_named_regime_shocks_are_scenario_inputs_not_verified_history(self) -> None:
        self.payload["market"]["historical_scenarios"] = {
            "2008": {"AAA":-.5,"BBB":-.4},
            "2020": {"AAA":-.34,"BBB":-.3},
            "2022": {"AAA":-.25,"BBB":-.15},
        }
        self.payload["constraints"]["max_named_scenario_loss_fraction"] = .30
        result = self.run_case()
        self.assertEqual(result["decision"],"BLOCK")
        self.assertIn("NAMED_STRESS_SCENARIO_LIMIT",result["block_reasons"])
        self.assertFalse(result["risk"]["historical_named_regimes_available"])
        self.assertLess(result["risk"]["user_supplied_regime_shocks_illustrative"]["2008"], -.3)

    def test_stale_or_unverified_holding_mark_cannot_enter_oms(self) -> None:
        for update in ({"mark_age_seconds":121},{"mark_source":"unverified_broker"}):
            with self.subTest(update=update):
                p=copy.deepcopy(self.payload)
                p["account"]["positions"][0].update(update)
                out=self.run_case(p)
                self.assertEqual(out["decision"],"BLOCK")
                self.assertIn("STALE_OR_UNVERIFIED_HOLDING_MARK",out["block_reasons"][0])

    def test_existing_unsettled_balances_are_not_silently_ignored(self) -> None:
        for key in ("unsettled_receivable","unsettled_payable"):
            with self.subTest(key=key):
                p=copy.deepcopy(self.payload)
                p["account"][key]=100
                out=self.run_case(p)
                self.assertEqual(out["decision"],"BLOCK")
                self.assertIn("UNSETTLED_ACCOUNT_BALANCES_REQUIRE_FULL_LEDGER_ADAPTER",
                              out["block_reasons"][0])

    def test_cli_output_stays_json_and_legacy_commands_remain_registered(self) -> None:
        from atlaspm.cli import build_parser
        parser=build_parser()
        parser.parse_args(["portfolio-engine","--file",str(ROOT/"examples"/"unified_portfolio_synthetic.json")])
        parser.parse_args(["analyze","--file","any.json"])
        self.assertEqual(main(["portfolio-engine","--file",str(ROOT/"examples"/"unified_portfolio_synthetic.json")]),0)


if __name__ == "__main__":
    unittest.main()
