"""v0.4.15: compare two UNSIGNED local risk-chain audit snapshots.

A SHA-256 digest here is only a local content fingerprint, not IBKR
provenance, timestamp attestation, broker margin approval or account risk
confirmation. No network, trading execution or automatic authorization.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


STAGES = ("monitor", "financing", "stress", "multiasset", "reverse")
COUNT_FIELDS = (
    "hypothetical_stress_alert_count",
    "hypothetical_multiasset_alert_count",
    "hypothetical_reverse_threshold_count",
)
VALID_STATUSES = {
    "CHAIN_INCONSISTENT",
    "HYPOTHETICAL_ALERTS_PRESENT",
    "HYPOTHETICAL_CLEAR_NOT_VERIFIED",
}
FLAGS = (
    "broker_source_independently_authenticated",
    "IBKR_financing_debt_separately_verified",
    "broker_treasury_collateral_verified",
    "IBKR_order_bound_whatif_verified",
    "ready_for_institutional_risk_gate",
    "live_execution_enabled",
)
METHOD = "atlaspm_local_ibkr_risk_chain_audit"
DIGEST_METHOD = "sha256_canonical_sorted_json_unsigned"
DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def _audit(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Risk audit input must be a JSON object")
    if value.get("method") != METHOD or value.get("source_digest_method") != DIGEST_METHOD:
        raise ValueError("Unsupported risk audit schema")
    if value.get("status") not in VALID_STATUSES:
        raise ValueError("Invalid previous/current risk audit status")
    acc = value.get("account_id")
    if not isinstance(acc, str) or not 1 <= len(acc) <= 64:
        raise ValueError("Missing local audit account identity")
    if any(value.get(flag) is not False for flag in FLAGS):
        raise ValueError("Audit may not claim verified broker data or trading authority")
    if not isinstance(value.get("baseline_comparison_performed"), bool):
        raise ValueError("Invalid audit baseline comparison flag")
    if value.get("upstream_monitor_state") not in {"NORMAL", "WATCH", "CRITICAL", "BLOCKED_DATA"}:
        raise ValueError("Missing or unsupported monitor state")
    issues = value.get("integrity_issues")
    if not isinstance(issues, list) or len(issues) > 200 or not all(
        isinstance(code, str) and 1 <= len(code) <= 100 for code in issues
    ):
        raise ValueError("Malformed integrity issue list")
    if (value["status"] == "CHAIN_INCONSISTENT") != bool(issues):
        raise ValueError("Audit status contradicts its integrity issue list")
    hashes = value.get("source_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(STAGES):
        raise ValueError("Audit is missing its five canonical SHA-256 digests")
    if not all(isinstance(hashes[stage], str) and DIGEST.fullmatch(hashes[stage])
               for stage in STAGES):
        raise ValueError("Invalid SHA-256 digest in audit report")
    counts: dict[str, int] = {}
    for field in COUNT_FIELDS:
        n = value.get(field)
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 20:
            raise ValueError("Malformed hypothetical alert count")
        counts[field] = n
    has_risk = any(counts.values()) or value["upstream_monitor_state"] in {"WATCH", "CRITICAL"}
    if value["status"] != "CHAIN_INCONSISTENT":
        expected = "HYPOTHETICAL_ALERTS_PRESENT" if has_risk else "HYPOTHETICAL_CLEAR_NOT_VERIFIED"
        if value["status"] != expected:
            raise ValueError("Audit status contradicts hypothetical risk counts")
    return value


def compare_ibkr_risk_audits(
    previous_data: Mapping[str, Any], current_data: Mapping[str, Any],
) -> dict[str, Any]:
    """Report source/summary differences; never mark either audit trusted."""
    old = _audit(previous_data, "previous")
    new = _audit(current_data, "current")
    if old["account_id"] != new["account_id"]:
        raise ValueError("Risk audit reports refer to different private accounts")

    changed = [
        stage for stage in STAGES
        if old["source_sha256"][stage] != new["source_sha256"][stage]
    ]
    deltas = {
        field: new[field] - old[field] for field in COUNT_FIELDS
    }
    count_changed = any(deltas.values())
    status_changed = old["status"] != new["status"]
    monitor_changed = old["upstream_monitor_state"] != new["upstream_monitor_state"]
    issues: list[str] = []
    if old["status"] == "CHAIN_INCONSISTENT":
        issues.append("PREVIOUS_AUDIT_CHAIN_INCONSISTENT")
    if new["status"] == "CHAIN_INCONSISTENT":
        issues.append("CURRENT_AUDIT_CHAIN_INCONSISTENT")
    if not changed and count_changed:
        issues.append("COUNTS_CHANGED_WITHOUT_SOURCE_DIGEST_CHANGE")
    if not changed and status_changed:
        issues.append("STATUS_CHANGED_WITHOUT_SOURCE_DIGEST_CHANGE")
    if not changed and monitor_changed:
        issues.append("MONITOR_STATE_CHANGED_WITHOUT_SOURCE_DIGEST_CHANGE")
    # An auditor could edit issues, status and counts while leaving report hashes
    # unchanged. Compare issue vocabulary but never echo untrusted issue text.
    if not changed and old["integrity_issues"] != new["integrity_issues"]:
        issues.append("INTEGRITY_ISSUES_CHANGED_WITHOUT_SOURCE_DIGEST_CHANGE")

    if issues:
        state = "REVIEW_REQUIRED_AUDIT_CONFLICT"
    elif changed:
        state = "REVIEW_REQUIRED_CHANGED_EVIDENCE"
    else:
        state = "NO_SOURCE_CHANGES_DETECTED_UNVERIFIED"

    if count_changed:
        count_direction = (
            "MIXED_HYPOTHETICAL_ALERT_CHANGE"
            if any(x > 0 for x in deltas.values()) and any(x < 0 for x in deltas.values())
            else "HYPOTHETICAL_ALERT_COUNTS_HIGHER"
            if any(x > 0 for x in deltas.values()) else
            "HYPOTHETICAL_ALERT_COUNTS_LOWER_UNVERIFIED"
        )
    else:
        count_direction = "HYPOTHETICAL_ALERT_COUNTS_UNCHANGED"

    return {
        "method": "atlaspm_offline_ibkr_audit_diff",
        "status": state,
        "account_id": old["account_id"],
        "changed_source_stages": changed,
        "source_digest_change_count": len(changed),
        "hypothetical_alert_count_direction": count_direction,
        "hypothetical_alert_count_deltas": deltas,
        "previous_audit_status": old["status"],
        "current_audit_status": new["status"],
        "previous_monitor_state": old["upstream_monitor_state"],
        "current_monitor_state": new["upstream_monitor_state"],
        "diff_issues": sorted(set(issues)),
        "previous_source_sha256": dict(old["source_sha256"]),
        "current_source_sha256": dict(new["source_sha256"]),
        "broker_source_independently_authenticated": False,
        "IBKR_financing_debt_separately_verified": False,
        "broker_treasury_collateral_verified": False,
        "IBKR_order_bound_whatif_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Both input audits are unsigned, local and user-controlled; no broker authenticity, independent account verification or historical order is proven.",
            "SHA-256 equality only compares stored claims: source reports and both audit files can be changed together.",
            "A fall in simulated alert counts is not evidence of improved real-world portfolio risk, broker credit or reduced liquidation probability.",
            "No source change detected does not imply that the source was ever authentic, accurate, complete or fresh.",
            "No scenario change or audit status creates trade approval, financing permission or risk-gate readiness.",
        ],
    }


def redacted_audit_diff_summary(report: Mapping[str, Any], output: str) -> dict[str, Any]:
    return {
        "written": output,
        "method": report["method"],
        "status": report["status"],
        "account_hint": "****" + str(report["account_id"])[-4:],
        "changed_source_stages": report["changed_source_stages"],
        "source_digest_change_count": report["source_digest_change_count"],
        "hypothetical_alert_count_direction": report["hypothetical_alert_count_direction"],
        "hypothetical_alert_count_deltas": report["hypothetical_alert_count_deltas"],
        "diff_issues": report["diff_issues"],
        "broker_source_independently_authenticated": False,
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Only masked account suffix, aggregate alert-count deltas and fixed codes are displayed.",
            "Audit comparison does not certify IBKR data origin, improved risk or trade eligibility.",
        ],
    }
