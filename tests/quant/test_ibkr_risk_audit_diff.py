"""v0.4.15 regression tests for local audit-to-audit comparisons."""

import copy
import json
import unittest

from atlaspm.ibkr_risk_chain_audit import audit_ibkr_risk_chain
from atlaspm.ibkr_risk_audit_diff import (
    compare_ibkr_risk_audits, redacted_audit_diff_summary,
)
from test_ibkr_risk_chain_audit import synthetic_chain


class RiskAuditDiffTests(unittest.TestCase):
    def setUp(self):
        self.reports = synthetic_chain()
        self.previous = audit_ibkr_risk_chain(self.reports)
        self.current = copy.deepcopy(self.previous)

    def compare(self, current=None, previous=None):
        return compare_ibkr_risk_audits(
            self.previous if previous is None else previous,
            self.current if current is None else current,
        )

    def test_identical_reports_no_source_change_but_still_unverified(self):
        report = self.compare()
        self.assertEqual(report["status"], "NO_SOURCE_CHANGES_DETECTED_UNVERIFIED")
        self.assertEqual(report["changed_source_stages"], [])
        self.assertEqual(report["hypothetical_alert_count_direction"],
                         "HYPOTHETICAL_ALERT_COUNTS_UNCHANGED")
        self.assertEqual(report["diff_issues"], [])
        for key in (
            "broker_source_independently_authenticated",
            "IBKR_financing_debt_separately_verified",
            "broker_treasury_collateral_verified",
            "IBKR_order_bound_whatif_verified",
            "ready_for_institutional_risk_gate",
            "live_execution_enabled",
        ):
            self.assertFalse(report[key])

    def test_changed_scenario_label_is_review_not_broker_risk_improvement(self):
        changes = copy.deepcopy(self.reports)
        changes["reverse"]["cases"][0]["private_name"] += "_modified"
        new_audit = audit_ibkr_risk_chain(changes)
        report = self.compare(current=new_audit)
        self.assertEqual(report["status"], "REVIEW_REQUIRED_CHANGED_EVIDENCE")
        self.assertEqual(report["changed_source_stages"], ["reverse"])
        self.assertEqual(report["source_digest_change_count"], 1)
        self.assertEqual(report["diff_issues"], [])
        self.assertFalse(report["ready_for_institutional_risk_gate"])

    def test_more_and_fewer_simulated_alerts_never_become_trade_approval(self):
        more = copy.deepcopy(self.current)
        more["source_sha256"]["stress"] = "f" * 64
        more["hypothetical_stress_alert_count"] += 1
        report = self.compare(current=more)
        self.assertEqual(report["status"], "REVIEW_REQUIRED_CHANGED_EVIDENCE")
        self.assertEqual(report["hypothetical_alert_count_direction"],
                         "HYPOTHETICAL_ALERT_COUNTS_HIGHER")

        fewer = copy.deepcopy(self.current)
        fewer["source_sha256"]["multiasset"] = "f" * 64
        fewer["hypothetical_multiasset_alert_count"] -= 1
        report = self.compare(current=fewer)
        self.assertEqual(report["hypothetical_alert_count_direction"],
                         "HYPOTHETICAL_ALERT_COUNTS_LOWER_UNVERIFIED")
        self.assertFalse(report["live_execution_enabled"])

    def test_mixed_hypothetical_count_deltas(self):
        changed = copy.deepcopy(self.current)
        changed["source_sha256"]["monitor"] = "a" * 64
        changed["hypothetical_stress_alert_count"] += 1
        changed["hypothetical_multiasset_alert_count"] -= 1
        report = self.compare(current=changed)
        self.assertEqual(report["hypothetical_alert_count_direction"],
                         "MIXED_HYPOTHETICAL_ALERT_CHANGE")

    def test_changed_counts_without_digest_changes_flagged_as_conflict(self):
        changed = copy.deepcopy(self.current)
        changed["hypothetical_stress_alert_count"] += 1
        report = self.compare(current=changed)
        self.assertEqual(report["status"], "REVIEW_REQUIRED_AUDIT_CONFLICT")
        self.assertIn("COUNTS_CHANGED_WITHOUT_SOURCE_DIGEST_CHANGE",
                      report["diff_issues"])

    def test_changed_status_without_digest_change_flagged(self):
        changed = copy.deepcopy(self.current)
        changed["status"] = "CHAIN_INCONSISTENT"
        changed["integrity_issues"] = ["SENSITIVE_INTERNAL_TOKEN"]
        report = self.compare(current=changed)
        self.assertEqual(report["status"], "REVIEW_REQUIRED_AUDIT_CONFLICT")
        self.assertIn("CURRENT_AUDIT_CHAIN_INCONSISTENT", report["diff_issues"])
        self.assertIn("STATUS_CHANGED_WITHOUT_SOURCE_DIGEST_CHANGE",
                      report["diff_issues"])
        self.assertNotIn("SENSITIVE_INTERNAL_TOKEN",
                         json.dumps(redacted_audit_diff_summary(report, "local_data/diff.json")))

    def test_previous_chain_inconsistent_blocks_conclusion(self):
        old = copy.deepcopy(self.previous)
        old["status"] = "CHAIN_INCONSISTENT"
        old["integrity_issues"] = ["GENERIC_ERROR"]
        report = self.compare(previous=old)
        self.assertIn("PREVIOUS_AUDIT_CHAIN_INCONSISTENT", report["diff_issues"])
        self.assertEqual(report["status"], "REVIEW_REQUIRED_AUDIT_CONFLICT")

    def test_account_identity_mismatch_rejected(self):
        changed = copy.deepcopy(self.current)
        changed["account_id"] = "SOME_OTHER_ACCOUNT"
        with self.assertRaisesRegex(ValueError, "different private accounts"):
            self.compare(current=changed)

    def test_invalid_hash_and_inconsistent_status_rejected(self):
        for invalid in (None, 0, "Z" * 64, "1234"):
            changed = copy.deepcopy(self.current)
            changed["source_sha256"]["stress"] = invalid
            with self.subTest(invalid=str(invalid)), self.assertRaises(ValueError):
                self.compare(current=changed)
        changed = copy.deepcopy(self.current)
        changed["status"] = "HYPOTHETICAL_CLEAR_NOT_VERIFIED"
        with self.assertRaisesRegex(ValueError, "contradicts"):
            self.compare(current=changed)

    def test_invalid_counts_and_approval_claims_rejected(self):
        for value in (True, -1, 999, 2.5):
            changed = copy.deepcopy(self.current)
            changed["hypothetical_stress_alert_count"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.compare(current=changed)
        for flag in (
            "live_execution_enabled", "ready_for_institutional_risk_gate",
            "IBKR_financing_debt_separately_verified",
            "broker_source_independently_authenticated",
        ):
            changed = copy.deepcopy(self.current)
            changed[flag] = True
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.compare(current=changed)

    def test_redacted_diff_output_has_no_private_hashes_or_account(self):
        changed = copy.deepcopy(self.current)
        changed["source_sha256"]["financing"] = "f" * 64
        report = self.compare(current=changed)
        text = json.dumps(redacted_audit_diff_summary(
            report, "local_data/ibkr_audit_diff.json"
        ))
        for private in ("DU1234567", "123950", "49950", "SYN_EQ_A", "f" * 64):
            self.assertNotIn(private, text)
        self.assertEqual(json.loads(text)["account_hint"], "****4567")


if __name__ == "__main__":
    unittest.main()
