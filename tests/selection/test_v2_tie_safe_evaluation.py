"""Tie-safe Top-N evaluation and fail-closed V2 research gate (offline)."""

from __future__ import annotations

import unittest

from atlaspm.research.ai_stock_ranker_v2 import _rank_evaluation, _model_research_review


def cohort(day: str, predictions: list[float]):
    names = "ABCDE"
    returns = [0.01, 0.02, 0.03, 0.04, 0.05]
    rows = [
        {
            "date": day,
            "symbol": symbol,
            "features": [float(i + 1)] + [0.0] * 8,
            "forward_excess_return": returns[i],
        }
        for i, symbol in enumerate(names)
    ]
    return rows, predictions


class V2TieSafeEvaluationTest(unittest.TestCase):
    def test_all_tied_top_n_does_not_claim_legacy_alphabetical_alpha(self):
        rows, scores = cohort("2026-07-01", [10, 10, 10, 9, 8])
        result = _rank_evaluation(rows, scores, top=2)
        self.assertEqual(result["decision_dates"], 1)
        self.assertEqual(result["top_n_ambiguous_decision_dates"], 1)
        self.assertEqual(result["top_n_unambiguous_decision_dates"], 0)
        self.assertEqual(result["top_n_selection_quality"], "ALL_AMBIGUOUS")
        self.assertIsNone(result["mean_top_n_forward_excess_return"])
        self.assertIsNone(result["mean_top_n_minus_same_universe_mean"])
        self.assertTrue(
            result["legacy_symbol_tiebroken_top_n_diagnostic"]["not_a_valid_portfolio_result"]
        )
        self.assertAlmostEqual(
            result["legacy_symbol_tiebroken_top_n_diagnostic"]["mean_forward_excess_return"], 0.015
        )
        self.assertEqual(result["simple_momentum_top_n"]["unambiguous_decision_dates"], 1)
        self.assertAlmostEqual(
            result["simple_momentum_top_n"]["mean_forward_excess_return"], 0.045
        )

    def test_unambiguous_model_top_n_means_are_reported(self):
        rows, scores = cohort("2026-07-01", [5, 4, 3, 2, 1])
        result = _rank_evaluation(rows, scores, top=2)
        self.assertEqual(result["top_n_unambiguous_decision_dates"], 1)
        self.assertEqual(result["top_n_ambiguous_decision_dates"], 0)
        self.assertEqual(result["top_n_selection_quality"], "ALL_UNAMBIGUOUS")
        self.assertAlmostEqual(result["mean_top_n_forward_excess_return"], 0.015)
        self.assertAlmostEqual(result["mean_top_n_minus_same_universe_mean"], -0.015)
        self.assertAlmostEqual(
            result["simple_momentum_top_n"]["mean_minus_same_universe_mean"], 0.015
        )

    def test_mixed_cohorts_only_count_eligible_top_n(self):
        r1, s1 = cohort("2026-07-01", [10, 10, 10, 9, 8])
        r2, s2 = cohort("2026-08-03", [5, 4, 3, 2, 1])
        result = _rank_evaluation(r1 + r2, s1 + s2, top=2)
        self.assertEqual(result["decision_dates"], 2)
        self.assertEqual(result["top_n_ambiguous_decision_dates"], 1)
        self.assertEqual(result["top_n_unambiguous_decision_dates"], 1)
        self.assertEqual(result["top_n_selection_quality"], "PARTIALLY_AMBIGUOUS")
        self.assertAlmostEqual(result["mean_top_n_forward_excess_return"], 0.015)
        self.assertEqual(result["simple_momentum_top_n"]["unambiguous_decision_dates"], 2)

    def test_gate_rejects_negative_ic_and_ambiguous_small_test(self):
        rows, scores = cohort("2026-07-01", [10, 10, 10, 9, 8])
        v = _rank_evaluation(rows, scores, top=2)
        t = _rank_evaluation(rows, scores, top=2)
        review = _model_research_review(v, t)
        self.assertEqual(review["status"], "REVIEW_REQUIRED")
        self.assertTrue(review["no_trading_authorization"])
        self.assertTrue(any("NO_UNAMBIGUOUS" in x for x in review["reasons"]))
        self.assertTrue(any("TOO_FEW_DECISION_DATES" in x for x in review["reasons"]))
        self.assertTrue(any("TOP_N_BOUNDARY_TIES" in x for x in review["reasons"]))

    def test_gate_remains_research_only_even_when_no_flags(self):
        rows, scores = cohort("2026-07-01", [5, 4, 3, 2, 1])
        valid = _rank_evaluation(rows, scores, top=2)
        # Construct the sufficient-period positive-IC diagnostic case without
        # pretending a five-stock fixture has 12 real independent dates.
        valid["decision_dates"] = 12
        valid["top_n_unambiguous_decision_dates"] = 12
        valid["mean_spearman_ic"] = 0.12
        valid["simple_momentum_mean_spearman_ic"] = 0.08
        review = _model_research_review(valid, valid)
        self.assertEqual(review["status"], "PRELIMINARY_RESEARCH_REVIEW")
        self.assertTrue(review["no_trading_authorization"])


if __name__ == "__main__":
    unittest.main()
