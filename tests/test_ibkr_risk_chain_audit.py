"""Offline v0.4.14 multi-stage audit and local digest regression tests."""

import copy
import json
import unittest
from pathlib import Path

from atlaspm.ibkr_gateway import FixtureGatewayTransport, collect_ibkr_readonly_account
from atlaspm.ibkr_financing import reconcile_ibkr_financing_margin
from atlaspm.ibkr_margin_monitor import collect_ibkr_margin_monitor
from atlaspm.ibkr_margin_stress import run_margin_stress
from atlaspm.ibkr_multiasset_budget import analyze_multiasset_risk_budget
from atlaspm.ibkr_reverse_stress import reverse_margin_stress
from atlaspm.ibkr_risk_chain_audit import (
    audit_ibkr_risk_chain, redacted_audit_summary,
)

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "DU1234567"


def synthetic_chain():
    def sample(name):
        return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))

    fixture = sample("ibkr_gateway_synthetic.json")
    capture = collect_ibkr_readonly_account(
        FixtureGatewayTransport(fixture), account_id=ACCOUNT, fixture_mode=True,
    )
    monitor = collect_ibkr_margin_monitor(
        FixtureGatewayTransport(fixture), account_id=ACCOUNT, fixture_mode=True,
    )
    financing = reconcile_ibkr_financing_margin(
        capture, sample("ibkr_mapping_synthetic.json"),
        sample("ibkr_financing_synthetic.json"),
    )
    stress = run_margin_stress(
        monitor, financing, sample("ibkr_margin_stress_synthetic.json")
    )
    multi = analyze_multiasset_risk_budget(
        stress, sample("ibkr_multiasset_budget_synthetic.json")
    )
    reverse = reverse_margin_stress(
        multi, sample("ibkr_reverse_stress_synthetic.json")
    )
    return {
        "monitor": monitor,
        "financing": financing,
        "stress": stress,
        "multiasset": multi,
        "reverse": reverse,
    }


class IBKRRiskChainAuditTests(unittest.TestCase):
    def setUp(self):
        self.reports = synthetic_chain()

    def test_clean_synthetic_evidence_chain_reports_hypothetical_alerts(self):
        result = audit_ibkr_risk_chain(self.reports)
        self.assertEqual(result["status"], "HYPOTHETICAL_ALERTS_PRESENT")
        self.assertEqual(result["integrity_issues"], [])
        self.assertEqual(result["hypothetical_stress_alert_count"], 1)
        self.assertEqual(result["hypothetical_multiasset_alert_count"], 2)
        self.assertEqual(result["hypothetical_reverse_threshold_count"], 2)
        self.assertEqual(set(result["source_sha256"]), set(self.reports))
        self.assertTrue(all(len(s) == 64 for s in result["source_sha256"].values()))
        for flag in (
            "broker_source_independently_authenticated",
            "IBKR_financing_debt_separately_verified",
            "broker_treasury_collateral_verified",
            "IBKR_order_bound_whatif_verified",
            "ready_for_institutional_risk_gate",
            "live_execution_enabled",
        ):
            self.assertIs(result[flag], False)

    def test_repeated_audit_digests_are_stable(self):
        first = audit_ibkr_risk_chain(self.reports)
        second = audit_ibkr_risk_chain(self.reports, baseline_audit=first)
        self.assertEqual(second["integrity_issues"], [])
        self.assertTrue(second["baseline_comparison_performed"])
        self.assertEqual(first["source_sha256"], second["source_sha256"])

    def test_changed_scenario_only_is_detected_against_baseline(self):
        baseline = audit_ibkr_risk_chain(self.reports)
        changed = copy.deepcopy(self.reports)
        changed["reverse"]["cases"][0]["private_name"] = "locally amended assumption"
        without_baseline = audit_ibkr_risk_chain(changed)
        self.assertFalse(without_baseline["integrity_issues"])
        second = audit_ibkr_risk_chain(changed, baseline_audit=baseline)
        self.assertEqual(second["status"], "CHAIN_INCONSISTENT")
        self.assertIn(
            "SOURCE_CHANGED_SINCE_BASELINE_REVERSE", second["integrity_issues"]
        )
        self.assertFalse(second["broker_source_independently_authenticated"])

    def test_cross_report_account_mismatch_is_fail_closed(self):
        reports = copy.deepcopy(self.reports)
        reports["reverse"]["account_id"] = "DIFFERENT_ACCOUNT"
        result = audit_ibkr_risk_chain(reports)
        self.assertIn("ACCOUNT_ID_MISMATCH_REVERSE", result["integrity_issues"])
        self.assertEqual(result["status"], "CHAIN_INCONSISTENT")

    def test_cross_report_loan_mismatch_is_fail_closed(self):
        reports = copy.deepcopy(self.reports)
        reports["stress"]["financing_principal_usd_claim"] += 12
        result = audit_ibkr_risk_chain(reports)
        self.assertIn("FINANCING_STRESS_LOAN_DRIFT", result["integrity_issues"])
        self.assertEqual(result["status"], "CHAIN_INCONSISTENT")

    def test_broker_verification_and_execution_flags_do_not_upgrade(self):
        for name in self.reports:
            for flag in (
                "ready_for_institutional_risk_gate",
                "live_execution_enabled",
                "IBKR_financing_debt_separately_verified",
            ):
                case = copy.deepcopy(self.reports)
                case[name][flag] = True
                with self.subTest(name=name, flag=flag):
                    report = audit_ibkr_risk_chain(case)
                    self.assertEqual(report["status"], "CHAIN_INCONSISTENT")
                    self.assertFalse(report["live_execution_enabled"])

    def test_stress_nlv_and_excess_formula_edits_are_detected(self):
        for changed_field, reason in (
            ("hypothetical_nlv_usd", "STRESS_NLV_FORMULA_DRIFT"),
            ("hypothetical_excess_liquidity_usd", "STRESS_EXCESS_FORMULA_DRIFT"),
        ):
            case = copy.deepcopy(self.reports)
            case["stress"]["scenarios"][0][changed_field] += 50
            result = audit_ibkr_risk_chain(case)
            self.assertIn(reason, result["integrity_issues"])

    def test_multiasset_pnl_var_and_interest_recomputed(self):
        for changed_field, reason in (
            ("assumed_signed_market_pnl_usd", "MULTI_ASSET_PNL_DRIFT"),
            ("one_day_99pct_delta_normal_var_usd", "MULTI_ASSET_VAR_DRIFT"),
            ("new_simple_financing_interest_usd", "MULTI_ASSET_INTEREST_DRIFT"),
        ):
            case = copy.deepcopy(self.reports)
            case["multiasset"]["scenarios"][1][changed_field] += 100
            result = audit_ibkr_risk_chain(case)
            self.assertIn(reason, result["integrity_issues"])

    def test_reverse_boundary_and_interest_tampering_detected(self):
        for field, reason in (
            ("earliest_threshold_fraction", "REVERSE_FIRST_THRESHOLD_DRIFT"),
            ("new_simple_interest_usd", "REVERSE_INTEREST_DRIFT"),
        ):
            case = copy.deepcopy(self.reports)
            case["reverse"]["cases"][1][field] += 0.25
            result = audit_ibkr_risk_chain(case)
            self.assertIn(reason, result["integrity_issues"])

    def test_invalid_nan_and_wrong_structure_rejected(self):
        case = copy.deepcopy(self.reports)
        case["monitor"]["margin_summary_usd_claim"]["net_liquidation"] = float("nan")
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            audit_ibkr_risk_chain(case)
        with self.assertRaisesRegex(ValueError, "Five named"):
            audit_ibkr_risk_chain({"monitor": self.reports["monitor"]})
        case = copy.deepcopy(self.reports)
        case["reverse"]["cases"][0]["index"] = True
        with self.assertRaises(ValueError):
            audit_ibkr_risk_chain(case)

    def test_audit_console_contains_no_amounts_or_asset_names(self):
        reports = copy.deepcopy(self.reports)
        reports["reverse"]["cases"][0]["private_name"] = "SENSITIVE_LABEL_999"
        audit = audit_ibkr_risk_chain(reports)
        summary = json.dumps(redacted_audit_summary(audit, "local_data/risk_audit.json"))
        for secret in ("DU1234567", "123950", "49950", "SYN_EQ_A", "SENSITIVE_LABEL_999"):
            self.assertNotIn(secret, summary)
        self.assertEqual(json.loads(summary)["account_hint"], "****4567")


if __name__ == "__main__":
    unittest.main()
