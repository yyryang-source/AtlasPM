"""Bounded, diagnostic-only ranking of historical evidence gaps."""
from __future__ import annotations

from collections import defaultdict

MAX_SYMBOLS = 50000
MAX_REPORT_ROWS = 1000


class EvidencePriority:
    """Accumulate counts from the exact same validated label-path query.

    Each input contains strict closing, identity, action-free and
    feature-history counts for each horizon; not unique rows across horizons.
    No identity is inferred, no source evidence is created.
    """
    def __init__(self, horizons, limit):
        if type(limit) is not int or not 1 <= limit <= MAX_REPORT_ROWS:
            raise ValueError("Evidence priority limit must be 1..1000")
        self.horizons = tuple(horizons)
        self.limit = limit
        self.counts = {}
        self.months = defaultdict(set)

    def add(self, month, symbol, counts):
        n = len(self.horizons)
        if len(counts) != 4*n:
            raise ValueError("EVIDENCE_PRIORITY_COUNT_SHAPE")
        if symbol not in self.counts:
            if len(self.counts) >= MAX_SYMBOLS:
                raise ValueError("EVIDENCE_PRIORITY_SYMBOL_BUDGET")
            self.counts[symbol] = [0]*(4*n)
        for j in range(n):
            strict, identity, action_free, history = (int(counts[k]) for k in
                (j, n+2*j, n+2*j+1, 3*n+j))
            if not 0 <= history <= action_free <= identity <= strict:
                raise ValueError("EVIDENCE_PRIORITY_COUNT_ORDER")
            if strict > history:
                self.months[symbol].add(month)
        for i, value in enumerate(counts):
            self.counts[symbol][i] += int(value)

    def finish(self, totals):
        n = len(self.horizons)
        sums = [sum(values[i] for values in self.counts.values()) for i in range(4*n)]
        for j, h in enumerate(self.horizons):
            t = totals[str(h)]
            expected = [t[k] for k in ("closing_path_labeled_feature_complete_rows",
                "reviewed_identity_path_rows", "reviewed_identity_action_free_rows",
                "reviewed_feature_history_rows")]
            actual = [sums[k] for k in (j, n+2*j, n+2*j+1, 3*n+j)]
            if actual != expected:
                raise ValueError("EVIDENCE_PRIORITY_RECONCILIATION_FAILED")
        rows = []
        for symbol, values in self.counts.items():
            gaps = {}
            for j, h in enumerate(self.horizons):
                strict, identity, action_free, history = (values[k] for k in
                    (j, n+2*j, n+2*j+1, 3*n+j))
                gaps[str(h)] = {
                    "closing_path_rows": strict, "identity_path_rows": identity,
                    "action_free_rows": action_free, "feature_history_rows": history,
                    "identity_gap": strict-identity,
                    "visible_action_exclusions": identity-action_free,
                    "feature_history_gap": action_free-history,
                }
            identity_gap = sum(x["identity_gap"] for x in gaps.values())
            action_gap = sum(x["visible_action_exclusions"] for x in gaps.values())
            history_gap = sum(x["feature_history_gap"] for x in gaps.values())
            if identity_gap or action_gap or history_gap:
                rows.append({"symbol": symbol, "horizons": gaps,
                             "identity_gap_horizon_rows": identity_gap,
                             "visible_action_excluded_horizon_rows": action_gap,
                             "feature_history_gap_horizon_rows": history_gap,
                             "affected_signal_months": sorted(self.months[symbol]),
                             "security_id": None, "valid_from": None, "valid_to": None})
        rows.sort(key=lambda r: (-r["identity_gap_horizon_rows"],
                                -r["feature_history_gap_horizon_rows"],
                                -r["visible_action_excluded_horizon_rows"], r["symbol"]))
        return {
            "schema": "atlaspm_evidence_priority_v1",
            "symbols_scanned": len(self.counts),
            "symbols_with_gaps": len(rows),
            "reported_symbols": min(self.limit, len(rows)),
            "truncated": len(rows) > self.limit,
            "all_counts_reconciled": True,
            "priority_rows": rows[:self.limit],
            "approved_for_model_training": False,
            "historical_security_identity_verified": False,
            "claims_ingested": 0,
            "warning": ("Gap counts are horizon-row counts, not unique observations or "
                        "recoverable training samples. Visible actions may require legitimate "
                        "exclusion, and feature gaps can reflect missing data or warmup. "
                        "No ticker-to-issuer inference or source certification."),
        }
