"""Independent audit-chain and post-fill portfolio guard regressions."""

import copy
import json
import unittest
from pathlib import Path

from atlaspm.oms_controls import (
    canonical_bytes, chain_audit_events, verify_audit_chain,
    post_fill_risk_snapshot,
)
from atlaspm.paper_oms import replay_paper_orders


ROOT = Path(__file__).resolve().parents[2] / "examples"


class OMSControlsTests(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads(
            (ROOT / "institutional_batch_ledger.json").read_text(encoding="utf-8")
        )
        self.plan = json.loads(
            (ROOT / "institutional_batch_plan.json").read_text(encoding="utf-8")
        )
        self.events = json.loads(
            (ROOT / "paper_oms_events.json").read_text(encoding="utf-8")
        )

    def replay(self):
        return replay_paper_orders(self.ledger, self.plan, self.events)

    def test_existing_replay_retains_previous_accounting(self):
        r = self.replay()
        self.assertEqual(r["decision"], "PAPER_REPLAY_COMPLETED")
        self.assertFalse(r["live_execution_enabled"])
        self.assertEqual(len(r["audit_events"]), 13)
        self.assertEqual(len(r["post_fill_risk_snapshots"]), 5)
        self.assertEqual(r["post_fill_risk_breaches"], [])
        self.assertTrue(r["audit_hash_chain_self_consistent"])
        self.assertFalse(r["broker_margin_rechecked_after_fills"])
        self.assertAlmostEqual(r["account_replay"]["marked_net_liquidation"], 123945)
        self.assertAlmostEqual(r["account_replay"]["net_liquidation_reconciliation_gap"], 0.0)
        self.assertTrue(verify_audit_chain(r["audit_events"], r["audit_root_sha256"]))
        self.assertEqual(r["audit_root_sha256"], self.replay()["audit_root_sha256"])

    def test_audit_chain_detects_modified_event_and_record(self):
        r = self.replay()
        modified = copy.deepcopy(r["audit_events"])
        modified[2]["source_event"]["quantity"] = 20000
        self.assertFalse(verify_audit_chain(modified, r["audit_root_sha256"]))
        modified = copy.deepcopy(r["audit_events"])
        modified[0]["before_state"] = "FILLED"
        self.assertFalse(verify_audit_chain(modified, r["audit_root_sha256"]))
        modified = copy.deepcopy(r["audit_events"])
        modified[0], modified[1] = modified[1], modified[0]
        self.assertFalse(verify_audit_chain(modified, r["audit_root_sha256"]))

    def test_canonical_bytes_reject_non_json_nan(self):
        with self.assertRaises(ValueError):
            canonical_bytes({"value": float("nan")})

    def test_same_audit_payload_creates_same_root(self):
        entries = [{"event_id": "A", "status": "SUBMITTED"},
                   {"event_id": "B", "status": "FILLED"}]
        chain, root = chain_audit_events(entries)
        self.assertTrue(verify_audit_chain(chain, root))
        self.assertFalse(verify_audit_chain(chain, "0" * 64))
        self.assertEqual(root, chain_audit_events(entries)[1])

    def test_postfill_risk_records_gross_and_ignores_put_mark(self):
        r = self.replay()
        final_risk = r["post_fill_risk_snapshots"][-1]
        self.assertEqual(final_risk["post_fill_risk_state"], "WITHIN_STATIC_LIMITS")
        self.assertAlmostEqual(final_risk["gross_to_net_liquidation"], 165190 / 123945)
        self.assertAlmostEqual(
            final_risk["conservative_stress_loss_fraction"],
            (57000 * .30 + 107690 * .06) / 123945
        )
        self.assertAlmostEqual(final_risk["long_put_mark_value_not_credited_to_stress"], 500)
        self.assertFalse(final_risk["broker_margin_or_whatif_revalidated"])

    def test_intermediate_ust_fill_halts_even_when_full_basket_passes(self):
        # A 1.34x policy allows the final balanced basket (~1.333x), but
        # purchasing the UST before reducing QQQ creates ~1.35x interim risk.
        self.plan["policy"]["max_gross_leverage"] = 1.34
        self.plan["risk_observation"]["pro_forma_gross_leverage"] = 1.333
        ust_only = [
            copy.deepcopy(e) for e in self.events["events"]
            if e["leg_id"] == "BUY-UST-10K"
        ]
        for i, e in enumerate(ust_only, 1):
            e["sequence"] = i
            e["event_id"] = f"SHOCK-{i}"
            e["timestamp"] = f"2026-11-20T21:0{i}:00Z"
        self.events["events"] = ust_only
        r = self.replay()
        self.assertEqual(r["decision"], "HALT_RISK")
        self.assertIn("POST_FILL_GROSS_LEVERAGE_EXCEEDS_BUDGET", r["post_fill_risk_breaches"])
        self.assertEqual(r["orders"][1]["status"], "FILLED")
        self.assertEqual(r["orders"][0]["status"], "NEW")
        self.assertEqual(len(r["post_fill_risk_snapshots"]), 1)
        self.assertTrue(verify_audit_chain(r["audit_events"], r["audit_root_sha256"]))
        self.events["events"].append({
            "sequence": 4, "event_id": "SHOCK-4",
            "timestamp": "2026-11-20T21:04:00Z",
            "type": "SUBMIT", "leg_id": "SELL-QQQ-10",
        })
        with self.assertRaisesRegex(ValueError, "freeze event replay"):
            self.replay()

    def test_direct_static_stress_violation(self):
        sample = [{"type": "equity", "market_value": 80000, "gross_notional": 80000}]
        risk = post_fill_risk_snapshot(
            sample, marked_net_liquidation=100000,
            settled_cash=20000, unsettled_payable=0, margin_debt=0,
            account_type="cash", max_gross_leverage=1,
            max_stress_loss_fraction=.10, equity_decline_fraction=.3,
            treasury_decline_fraction=.1,
        )
        self.assertIn("POST_FILL_STRESS_LOSS_EXCEEDS_BUDGET", risk["breaches"])
        self.assertEqual(risk["post_fill_risk_state"], "HALT_RISK")


if __name__ == "__main__":
    unittest.main()
