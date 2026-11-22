"""v0.4.16 dashboard safety, state derivation and redacted HTML tests."""

import copy
import json
import unittest

from atlaspm.ibkr_risk_chain_audit import audit_ibkr_risk_chain
from atlaspm.ibkr_risk_audit_diff import compare_ibkr_risk_audits
from atlaspm.ibkr_dashboard import (
    build_dashboard_view, render_dashboard_html, redacted_dashboard_console,
)
from .test_ibkr_risk_chain_audit import synthetic_chain


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.reports = synthetic_chain()
        self.previous = audit_ibkr_risk_chain(self.reports)
        self.latest = audit_ibkr_risk_chain(
            self.reports, baseline_audit=self.previous,
        )
        self.diff = compare_ibkr_risk_audits(self.previous, self.latest)

    def build(self, reports=None, previous=None, latest=None, diff=None):
        return build_dashboard_view(
            self.reports if reports is None else reports,
            self.previous if previous is None else previous,
            self.latest if latest is None else latest,
            self.diff if diff is None else diff,
        )

    def test_expected_synthetic_dashboard_status_and_zero_trade_authority(self):
        view = self.build()
        self.assertEqual(view["dashboard_state"], "LOCAL_SIMULATED_ALERTS_PRESENT")
        self.assertEqual(view["data_source_type"], "SYNTHETIC_FIXTURE")
        self.assertEqual(view["account_hint"], "****4567")
        self.assertEqual(len(view["stages"]), 5)
        self.assertEqual(view["validation_codes"], [])
        self.assertEqual([x["value"] for x in view["risk_counts"]], [1, 2, 2])
        self.assertEqual(view["audit_integrity_issue_count"], 0)
        self.assertEqual(view["changed_stages"], [])
        for flag in (
            "broker_source_independently_authenticated",
            "IBKR_financing_debt_separately_verified",
            "ready_for_institutional_risk_gate", "live_execution_enabled",
        ):
            self.assertFalse(view[flag])

    def test_html_is_standalone_and_has_clickable_details(self):
        html = render_dashboard_html(self.build())
        self.assertIn("<!doctype html>", html)
        self.assertIn("Content-Security-Policy", html)
        self.assertIn("connect-src 'none'", html)
        self.assertIn("script-src 'none'", html)
        self.assertIn('<details>', html)
        self.assertIn('id="stages"', html)
        self.assertIn('id="integrity"', html)
        self.assertIn("LOCAL", html.upper())
        self.assertIn("模拟风险预警", html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("https://", html.lower())
        self.assertNotIn("http://", html.lower())

    def test_sensitive_account_balances_symbols_and_private_labels_not_embedded(self):
        reports = copy.deepcopy(self.reports)
        reports["stress"]["scenarios"][0]["name"] = "TOP_SECRET_CASE_LABEL_987"
        # Recomputed local audit and comparison are made from the altered
        # scenario, so this is a valid internally consistent report set.
        p = audit_ibkr_risk_chain(reports)
        latest = audit_ibkr_risk_chain(reports, baseline_audit=p)
        diff = compare_ibkr_risk_audits(p, latest)
        view = self.build(reports=reports, previous=p, latest=latest, diff=diff)
        html = render_dashboard_html(view)
        console = json.dumps(redacted_dashboard_console(
            view, "local_data/atlaspm_dashboard.html",
        ))
        for secret in (
            "DU1234567", "123950", "110000", "49950", "SYN_EQ_A",
            "TOP_SECRET_CASE_LABEL_987", "loan_principal_from_statement",
            p["source_sha256"]["stress"],
        ):
            self.assertNotIn(secret, html)
            self.assertNotIn(secret, console)

    def test_stale_saved_diff_forces_local_review(self):
        changed = copy.deepcopy(self.diff)
        changed["source_digest_change_count"] = 1
        result = self.build(diff=changed)
        self.assertEqual(result["dashboard_state"], "LOCAL_REVIEW_REQUIRED")
        self.assertIn("SAVED_DIFF_REPORT_MISMATCH", result["validation_codes"])

    def test_changed_source_after_latest_audit_forces_local_review(self):
        reports = copy.deepcopy(self.reports)
        reports["reverse"]["cases"][0]["private_name"] = "changed locally"
        view = self.build(reports=reports)
        self.assertEqual(view["dashboard_state"], "LOCAL_REVIEW_REQUIRED")
        self.assertIn("LATEST_AUDIT_REPORT_MISMATCH", view["validation_codes"])
        html = render_dashboard_html(view)
        self.assertIn("需要人工核对", html)
        self.assertNotIn("changed locally", html)

    def test_corrupted_stress_accounting_rejected_as_local_review(self):
        reports = copy.deepcopy(self.reports)
        reports["stress"]["scenarios"][0]["hypothetical_nlv_usd"] += 10
        view = self.build(reports=reports)
        self.assertEqual(view["dashboard_state"], "LOCAL_REVIEW_REQUIRED")
        self.assertIn("CROSS_STAGE_AUDIT_INCONSISTENT", view["validation_codes"])

    def test_unrecognized_case_status_only_appears_as_fixed_label(self):
        reports = copy.deepcopy(self.reports)
        reports["multiasset"]["scenarios"][0]["state"] = "PRIVATE_LABEL_487"
        view = self.build(reports=reports)
        self.assertEqual(view["dashboard_state"], "LOCAL_REVIEW_REQUIRED")
        self.assertIn("SCENARIO_STATUS_NOT_ALLOWED", view["validation_codes"])
        html = render_dashboard_html(view)
        self.assertNotIn("PRIVATE_LABEL_487", html)
        self.assertIn("无法识别的状态", html)

    def test_approval_flag_provenance_claim_is_refused(self):
        reports = copy.deepcopy(self.reports)
        reports["monitor"]["ready_for_institutional_risk_gate"] = True
        view = self.build(reports=reports)
        self.assertEqual(view["dashboard_state"], "LOCAL_REVIEW_REQUIRED")
        self.assertIn("CROSS_STAGE_AUDIT_INCONSISTENT", view["validation_codes"])

    def test_incompatible_account_audits_are_rejected(self):
        old = copy.deepcopy(self.previous)
        old["account_id"] = "OTHER_ACCOUNT"
        with self.assertRaises(ValueError):
            self.build(previous=old)

    def test_empty_or_invalid_input_reports_are_rejected(self):
        with self.assertRaises(ValueError):
            self.build(reports={})
        reports = copy.deepcopy(self.reports)
        reports["reverse"]["cases"] = []
        with self.assertRaises(ValueError):
            self.build(reports=reports)


if __name__ == "__main__":
    unittest.main()
