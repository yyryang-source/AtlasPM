"""Model tournament tests: no hindsight, mature-only selection, safety and costs."""

import csv
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.ml_model_tournament import (
    CANDIDATES, backtest_model_tournament, choose_tournament_weights,
    score_matured_forecasts,
)


def write_synthetic_history(directory: str, *, mutate_final: bool = False) -> Path:
    path = Path(directory) / "fictional_prices.csv"
    base = {"A": 100.0, "B": 95.0, "C": 120.0}
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "symbol", "adjusted_close"])
        for day in range(215):
            for index, symbol in enumerate(base):
                if day:
                    scale = .004 if day < 70 else (.018 if day < 140 else .008)
                    base[symbol] *= 1 + (
                        scale*(1+.25*index)*math.sin(day*.19+index*.9)
                        + .0004*(index+1)
                    )
                observed = base[symbol]
                if mutate_final and day == 214 and symbol == "C":
                    observed *= 1.30
                writer.writerow([
                    (date(2020, 1, 1)+timedelta(days=day)).isoformat(),
                    symbol, f"{observed:.12f}"
                ])
    return path


def run(path: Path, **kwargs):
    params = dict(
        feature_days=20, forecast_days=5,
        minimum_training_samples=30,
        rebalance_days=10,
        minimum_validated_rebalances=3,
        validation_window=8,
    )
    params.update(kwargs)
    return backtest_model_tournament(path, **params)


class MatureOnlySelectionUnitTests(unittest.TestCase):
    def test_forward_label_not_yet_known_must_not_score(self):
        series = [[.01] * 70, [-.015] * 70]
        forecasts = {
            "trailing_vol": [.18, .23],
            "ridge": [.17, .23],
            "lightgbm": [.16, .22],
        }
        history = [
            {"signal_index": 20, "forecasts": forecasts},
            {"signal_index": 49, "forecasts": {
                "trailing_vol": [100.0, 100.0],
                "ridge": [0.00001, 0.00001],
                "lightgbm": [0.00001, 0.00001],
            }},
        ]
        at_25 = score_matured_forecasts(
            history, series, current_signal_index=25,
            forecast_days=5, window_rebalances=10, minimum_rebalances=1,
        )
        only_past = score_matured_forecasts(
            history[:1], series, current_signal_index=25,
            forecast_days=5, window_rebalances=10, minimum_rebalances=1,
        )
        self.assertEqual(at_25, only_past)
        self.assertEqual(at_25["latest_label_end_price_index"],25)
        self.assertEqual(at_25["completed_asset_predictions"],2)

    def test_model_selection_responds_to_matured_errors_not_final_return(self):
        validated = {
            "is_validated":True,
            "mae_by_model":{"trailing_vol":.06,"ridge":.03,"lightgbm":.02},
        }
        best = choose_tournament_weights(validated,mode="champion")
        self.assertEqual(best["champion"], "lightgbm")
        self.assertEqual(best["weights"]["lightgbm"], 1)
        blended = choose_tournament_weights(validated,mode="ensemble")
        self.assertAlmostEqual(sum(blended["weights"].values()),1)
        self.assertTrue(all(0 < v < 1 for v in blended["weights"].values()))

        weak = {
            "is_validated":True,
            "mae_by_model":{"trailing_vol":.03,"ridge":.0301,"lightgbm":.031},
        }
        for mode in ("champion","ensemble"):
            decision = choose_tournament_weights(weak, mode=mode)
            self.assertEqual(decision["champion"], "trailing_vol")
            self.assertEqual(decision["weights"]["trailing_vol"], 1)
        unready=choose_tournament_weights(
            {"is_validated":False}, mode="champion",
        )
        self.assertEqual(unready["champion"],"trailing_vol")
        self.assertEqual(unready["reason"],
                         "INSUFFICIENT_COMPLETED_VALIDATION_FALLBACK")

    def test_hysteresis_keeps_proven_previous_champion(self):
        validated = {
            "is_validated":True,
            "mae_by_model":{"trailing_vol":.05,"ridge":.0201,"lightgbm":.0200},
        }
        picked = choose_tournament_weights(
            validated, mode="champion", previous_champion="ridge",
            switch_margin=.02,
        )
        self.assertEqual(picked["champion"], "ridge")
        with self.assertRaisesRegex(ValueError, "unknown|Unknown"):
            choose_tournament_weights(validated, mode="champion",
                                      previous_champion="GHOST")

    def test_bad_window_and_scores_rejected(self):
        with self.assertRaises(ValueError):
            score_matured_forecasts(
                [], [[.01]*20], current_signal_index=15,
                forecast_days=5, window_rebalances=2,
                minimum_rebalances=4,
            )
        for bad in (
            {"trailing_vol":.03, "ridge":float("nan"), "lightgbm":.02},
            {"trailing_vol":-.01, "ridge":.03, "lightgbm":.02},
            {"trailing_vol":.03, "ridge":.02},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                choose_tournament_weights(
                    {"is_validated":True,"mae_by_model":bad},
                    mode="champion",
                )


class NativeTournamentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import lightgbm
        except (ImportError, OSError):
            raise unittest.SkipTest("Optional pip install -e '.[ml]' needed")

    def test_end_to_end_champion_is_cash_only_and_past_only(self):
        with tempfile.TemporaryDirectory() as directory:
            report=run(write_synthetic_history(directory), mode="champion")
        self.assertEqual(
            report["selection_policy"]["name"],
            "rolling_matured_prequential_risk_mae_v1"
        )
        self.assertGreater(len(report["rebalances"]),10)
        self.assertEqual(set(report["performance"]),{
            "selected","ridge_only","lightgbm_only",
            "naive_trailing_vol_risk","equal_weight_buy_and_hold"
        })
        self.assertGreater(report["model_selection"]
                           ["rebalances_without_completed_validation"],0)
        self.assertEqual(
            sum(report["model_selection"]["selection_count_by_champion"].values()),
            len(report["rebalances"]),
        )
        self.assertEqual(report["rebalances"][0]["selected_champion"],
                         "trailing_vol")
        self.assertTrue(any(
            r["validated_historical_prediction_dates"] >= 3
            for r in report["rebalances"]
        ))
        for row in report["rebalances"]:
            self.assertLess(row["signal_date"], row["execution_date"])
            self.assertLess(row["execution_date"], row["first_held_return_date"])
            end=row["latest_validation_target_end_date"]
            if end is not None:
                self.assertLessEqual(end,row["signal_date"])
            weights=row["selection_weights"]
            self.assertEqual(set(weights),set(CANDIDATES))
            self.assertAlmostEqual(sum(weights.values()),1)
            self.assertIn(row["selected_champion"],CANDIDATES)
            allocation=row["selected_allocation"]
            self.assertLessEqual(allocation["risky_gross"],1)
            self.assertGreaterEqual(allocation["cash_weight"],0)
            self.assertAlmostEqual(
                sum(allocation["account_weight_targets"].values())
                + allocation["cash_weight"],1,places=9
            )
            self.assertFalse(row["is_real_or_paper_broker_order"])
        for curve in report["equity_curves"].values():
            for point in curve:
                self.assertAlmostEqual(
                    point["cash_equity_index_units"]
                    + point["gross_holdings_equity_index_units"],
                    point["equity_index"], places=9,
                )
        self.assertFalse(report["model_validated_for_live_trading"])
        self.assertFalse(report["ready_for_institutional_risk_gate"])
        self.assertFalse(report["live_execution_enabled"])
        self.assertFalse(report["IBKR_financing_debt_separately_verified"])

    def test_ensemble_is_convex_and_allocation_uses_identical_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            report=run(
                write_synthetic_history(directory),
                mode="ensemble",target_annual_volatility=.065,
            )
        self.assertEqual(report["selected_model_family"],"ensemble")
        for row in report["rebalances"]:
            self.assertAlmostEqual(sum(row["selection_weights"].values()),1)
            self.assertTrue(all(
                0 <= x <= 1 for x in row["selection_weights"].values()
            ))
            self.assertLessEqual(
                row["selected_allocation"]["conservative_forecast_annual_risk"],
                .065 + 1e-9,
            )
        self.assertEqual(
            report["selection_policy"]["scoring_uses_only_matured_labels"],True
        )
        self.assertFalse(
            report["selection_policy"]["has_independently_untouched_final_test_set"]
        )

    def test_last_future_price_cannot_change_prior_selection(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            original=run(write_synthetic_history(d1))
            modified=run(write_synthetic_history(d2,mutate_final=True))
        self.assertEqual(
            [r["selection_weights"] for r in original["rebalances"]],
            [r["selection_weights"] for r in modified["rebalances"]],
        )
        self.assertEqual(
            [r["selected_allocation"] for r in original["rebalances"]],
            [r["selected_allocation"] for r in modified["rebalances"]],
        )
        self.assertNotEqual(
            original["performance"]["selected"]["ending_equity_index"],
            modified["performance"]["selected"]["ending_equity_index"],
        )

    def test_all_cash_hard_gate_still_dominates_best_ai(self):
        with tempfile.TemporaryDirectory() as directory:
            report=run(
                write_synthetic_history(directory),
                mode="champion", maximum_uniform_stress_loss=0,
            )
        self.assertTrue(all(
            row["selected_allocation"]["risky_gross"] == 0
            for row in report["rebalances"]
        ))
        self.assertAlmostEqual(
            report["performance"]["selected"]["ending_equity_index"],1
        )
        self.assertEqual(
            report["performance"]["selected"]["total_cost_equity_index_units"],0
        )

    def test_unacceptable_tournament_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            path=write_synthetic_history(directory)
            for bad in (
                {"mode":"best_peek_future"},
                {"validation_window":1},
                {"minimum_validated_rebalances":1},
                {"minimum_validated_rebalances":10,"validation_window":3},
                {"switch_margin":-.01},
                {"maximum_gross_exposure":4},
                {"maximum_name_weight":.2},
            ):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    run(path,**bad)


if __name__=="__main__":
    unittest.main()
