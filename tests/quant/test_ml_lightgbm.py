"""Optional real LightGBM training tests, skipped when [ml] extra is absent."""

import importlib.util
import math
import tempfile
import unittest

from atlaspm.research.ml_dynamic_rebalance import (
    _features, backtest_ml_dynamic_rebalance,
)
from atlaspm.research.ml_lightgbm import (
    train_lightgbm_risk, predict_lightgbm_risk,
)

HAVE_LIGHTGBM = importlib.util.find_spec("lightgbm") is not None


@unittest.skipUnless(HAVE_LIGHTGBM, "Install optional extra: pip install -e '.[ml]'")
class OpenSourceLightGBMTests(unittest.TestCase):

    def test_real_native_model_learns_and_persists_privately(self):
        observations = [
            .003 + .01*math.sin(i*.23) + .005*math.cos(i*.11)
            for i in range(150)
        ]
        trained = train_lightgbm_risk(
            observations, signal_index=100, lookback=20,
            horizon=5, minimum_samples=30,
        )
        self.assertEqual(trained["model"], "trained_lightgbm_log_forward_rms_risk")
        self.assertEqual(trained["training_samples"], 76)
        self.assertEqual(trained["label_fully_observed_through_price_index"],100)
        self.assertTrue(trained["trained_native_model_text_private"])
        self.assertEqual(len(trained["feature_importance_split"]),5)
        self.assertFalse(trained["is_official_qlib_pipeline"])
        self.assertFalse(trained["model_validated_for_live_trading"])
        forecast = predict_lightgbm_risk(trained, _features(observations, 100, 20))
        self.assertTrue(math.isfinite(forecast))
        self.assertGreater(forecast, 0)

    def test_future_return_mutation_does_not_change_model_or_prediction(self):
        observations = [
            .008 * math.sin(i*.17) + .0007 for i in range(150)
        ]
        training = train_lightgbm_risk(observations,90,20,5,30)
        changed = list(observations)
        changed[90:] = [0.15]*(len(changed)-90)
        training_again = train_lightgbm_risk(changed,90,20,5,30)
        self.assertEqual(training, training_again)
        first = predict_lightgbm_risk(training,_features(observations,90,20))
        second = predict_lightgbm_risk(training_again,_features(changed,90,20))
        self.assertAlmostEqual(first,second,places=10)

    def test_lightgbm_full_dynamic_cash_rebalance_no_broker(self):
        # The existing synthetic history has 2 entirely fictional assets.
        from pathlib import Path
        repo = Path(__file__).resolve().parents[2]
        data = backtest_ml_dynamic_rebalance(
            repo / "examples" / "synthetic_prices.csv",
            feature_days=20, forecast_days=5,
            minimum_training_samples=30,
            rebalance_days=10,
            model_family="lightgbm",
        )
        self.assertEqual(
            data["model_family"], "supervised_lightgbm_gbdt_log_forward_rms_risk"
        )
        self.assertEqual(data["selected_model_family"],"lightgbm")
        self.assertFalse(data["is_official_qlib_pipeline"])
        self.assertFalse(data["is_pretrained_institutional_model"])
        self.assertFalse(data["live_execution_enabled"])
        self.assertFalse(data["model_validated_for_live_trading"])
        self.assertGreater(len(data["rebalances"]),0)
        self.assertEqual(set(data["performance"]),
                         {"ai","naive_trailing_vol_risk","equal_weight_buy_and_hold"})
        for rebalance in data["rebalances"]:
            self.assertEqual(rebalance["model"],
                             "trained_lightgbm_log_forward_rms_risk")
            self.assertLessEqual(rebalance["ai_allocation"]["risky_gross"],1)
            self.assertAlmostEqual(
                sum(rebalance["ai_allocation"]["account_weight_targets"].values())
                + rebalance["ai_allocation"]["cash_weight"],1,places=10)
        for point in data["equity_curves"]["ai"]:
            self.assertAlmostEqual(point["gross_holdings_equity_index_units"]
                                   +point["cash_equity_index_units"],
                                   point["equity_index"],places=10)

    def test_missing_or_invalid_model_type_rejected(self):
        observations = [.01 * math.sin(i*.32)+.0001 for i in range(140)]
        trained = train_lightgbm_risk(observations,80,20,5,30)
        trained["model"]="IMPOSTER"
        with self.assertRaises(ValueError):
            predict_lightgbm_risk(trained,_features(observations,80,20))


class OptionalLightGBMDependencyTests(unittest.TestCase):
    def test_no_optional_package_needed_for_regular_ridge(self):
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / "examples/synthetic_prices.csv"
        report = backtest_ml_dynamic_rebalance(
            path, minimum_training_samples=30, model_family="ridge"
        )
        self.assertEqual(report["selected_model_family"],"ridge")
        self.assertEqual(report["model_family"],
                         "supervised_ridge_regression_log_forward_rms_risk")

    def test_invalid_or_unavailable_family_refused(self):
        from pathlib import Path
        path=Path(__file__).resolve().parents[2]/"examples/synthetic_prices.csv"
        with self.assertRaisesRegex(ValueError,"Supported supervised"):
            backtest_ml_dynamic_rebalance(path, model_family="fund-manager-secrets")


if __name__ == "__main__":
    unittest.main()
