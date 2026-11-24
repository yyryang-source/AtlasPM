"""Offline seven-stage lifecycle regression tests; no data or broker required."""
import json
import unittest

from atlaspm.framework.trading_lifecycle import (
    CONTROLS, NEXT, PHASES, LifecycleReview, Receipt, Step, architecture_report,
)


ENTRY = (
    Step.MANDATE, Step.INPUTS, Step.QUALITY, Step.RESEARCH, Step.UNIVERSE,
    Step.VALIDATION, Step.PLAN, Step.COST, Step.ALLOCATION, Step.RISK,
    Step.PREFLIGHT, Step.ORDER_REVIEW, Step.FILL_REVIEW, Step.POST_FILL,
)


def replay(steps):
    review = LifecycleReview("synthetic-review")
    for i, step in enumerate(steps):
        review = review.record(Receipt(step, f"fixture-{i}", "reviewed"))
    return review


class TradingLifecycleTests(unittest.TestCase):
    def test_registry(self):
        report = architecture_report()
        self.assertEqual([p.number for p in PHASES], list(range(1, 8)))
        self.assertEqual(set(NEXT), set(Step))
        self.assertIn("emergency_stop", CONTROLS)
        self.assertIn("portfolio_circuit_breaker", CONTROLS)
        self.assertFalse(report["trade_authorized"])
        self.assertFalse(report["live_execution_enabled"])
        self.assertFalse(report["automatic_broker_orders_enabled"])
        json.dumps(report)

    def test_complete_exit_cycle(self):
        steps = ENTRY + (
            Step.MONITOR, Step.MONITOR, Step.ADJUST, Step.COST, Step.ALLOCATION,
            Step.RISK, Step.PREFLIGHT, Step.ORDER_REVIEW, Step.FILL_REVIEW,
            Step.POST_FILL, Step.MONITOR, Step.EXIT, Step.COST, Step.ALLOCATION,
            Step.RISK, Step.PREFLIGHT, Step.ORDER_REVIEW, Step.FILL_REVIEW,
            Step.POST_FILL, Step.FLAT, Step.JOURNAL, Step.ATTRIBUTION,
            Step.MODEL_REVIEW,
        )
        review = replay(steps)
        self.assertEqual(review.status, "review_complete")
        self.assertEqual(review.next_steps, ())
        self.assertFalse(review.report()["evidence_independently_verified"])
        self.assertFalse(review.report()["trade_authorized"])
        with self.assertRaises(ValueError):
            review.record(Receipt(Step.MANDATE, "fresh", "reviewed"))

    def test_blocked_data_and_risk(self):
        for step in (Step.QUALITY, Step.RISK, Step.PREFLIGHT, Step.POST_FILL):
            with self.subTest(step=step):
                index = ENTRY.index(step)
                review = replay(ENTRY[:index])
                review = review.record(Receipt(step, "failure", "blocked", "missing evidence"))
                self.assertEqual(review.status, "blocked")
                self.assertEqual(review.next_steps, ())
                with self.assertRaises(ValueError):
                    review.record(Receipt(step, "retry", "reviewed"))

    def test_no_skipping_risk_or_reconciliation(self):
        review = replay(ENTRY[:ENTRY.index(Step.RISK)])
        with self.assertRaises(ValueError):
            review.record(Receipt(Step.ORDER_REVIEW, "skip", "reviewed"))
        review = replay(ENTRY[:-1])
        with self.assertRaises(ValueError):
            review.record(Receipt(Step.MONITOR, "skip", "reviewed"))
        review = replay(ENTRY + (Step.MONITOR, Step.EXIT))
        with self.assertRaises(ValueError):
            review.record(Receipt(Step.FLAT, "skip", "reviewed"))

    def test_no_fill_or_cancelled_entry_review(self):
        review = replay(ENTRY + (
            Step.FLAT, Step.JOURNAL, Step.ATTRIBUTION, Step.MODEL_REVIEW,
        ))
        self.assertEqual(review.status, "review_complete")
        self.assertFalse(review.report()["trade_authorized"])

    def test_invalid_receipts(self):
        with self.assertRaises(ValueError):
            Receipt(Step.MANDATE, " ", "reviewed")
        with self.assertRaises(ValueError):
            Receipt(Step.MANDATE, "ref", "blocked")
        with self.assertRaises(ValueError):
            Receipt(Step.MANDATE, "ref", "approved")
        review = replay((Step.MANDATE,))
        with self.assertRaises(ValueError):
            review.record(Receipt(Step.INPUTS, " fixture-0 ", "reviewed"))
        self.assertEqual(len(review.receipts), 1)
        with self.assertRaises(ValueError):
            LifecycleReview("bad", (Receipt(Step.RISK, "ref", "reviewed"),))


if __name__ == "__main__":
    unittest.main()
