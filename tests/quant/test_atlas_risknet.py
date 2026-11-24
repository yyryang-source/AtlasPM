"""AtlasPM true independently trained RiskNet v2 safety & forecasting tests."""

import importlib.util
import math
import tempfile
import unittest

from atlaspm.risk.atlas_risknet import (
    RISKNET_NAME, fit_atlas_risknet, predict_atlas_risknet,
)
from atlaspm.research.atlas_regimenet import (
    MODEL_NAME, fit_atlas_regimenet, native_feature_vector,
)
from .test_atlas_regimenet import synthetic_returns, make_prices, run

HAS_NUMPY = importlib.util.find_spec("numpy") is not None


@unittest.skipUnless(HAS_NUMPY, "Install pip install -e '.[native]'")
class OriginalRiskNetTests(unittest.TestCase):
    def test_true_single_head_fit_from_random_initialized_weights(self):
        returns = synthetic_returns()
        trained = fit_atlas_risknet(
            returns, 100, 20, 5, 60,
            train_window=70, sample_stride=2, epochs=20,
        )
        self.assertEqual(trained["model"], RISKNET_NAME)
        self.assertEqual(len(trained["targets"]), 1)
        self.assertFalse(trained["return_prediction_head_exists"])
        self.assertTrue(trained["trained_from_scratch"])
        self.assertFalse(trained["pretrained_weights_used"])
        self.assertLess(trained["final_data_loss"], trained["initial_data_loss"])
        self.assertEqual(trained["label_fully_observed_through_price_index"], 100)
        self.assertEqual(len(trained["gate_weights"][0]), 3)
        self.assertEqual(len(trained["expert_weights"]), 3)
        self.assertEqual(len(trained["expert_weights"][0]), len(trained["feature_means"]))
        prediction = predict_atlas_risknet(
            trained, native_feature_vector(returns, 100, 1, 20)
        )
        self.assertGreater(prediction["annualized_rms_risk_forecast"], 0)
        self.assertTrue(math.isfinite(prediction["annualized_rms_risk_forecast"]))
        self.assertIsNone(prediction["annualized_arithmetic_return_forecast"])
        self.assertAlmostEqual(sum(prediction["latent_expert_probabilities"]), 1)

        dual = fit_atlas_regimenet(
            returns,100,20,5,60,
            train_window=70,sample_stride=2,epochs=20,
        )
        self.assertEqual(dual["model"], MODEL_NAME)
        self.assertEqual(len(dual["targets"]),2)
        self.assertNotEqual(trained["model"], dual["model"])
        self.assertNotEqual(trained["expert_weights"],dual["expert_weights"])

    def test_risknet_training_is_unaffected_by_unobserved_future(self):
        returns = synthetic_returns()
        changed = [asset[:] for asset in returns]
        for asset in changed:
            for i in range(100,len(asset)):
                asset[i] = .10
        first = fit_atlas_risknet(
            returns,100,20,5,60,train_window=70,epochs=20
        )
        second = fit_atlas_risknet(
            changed,100,20,5,60,train_window=70,epochs=20
        )
        self.assertEqual(first,second)
        self.assertEqual(
            predict_atlas_risknet(first,native_feature_vector(returns,100,0,20)),
            predict_atlas_risknet(second,native_feature_vector(changed,100,0,20)),
        )
        self.assertLessEqual(first["label_fully_observed_through_price_index"],100)

    def test_risknet_only_backtest_has_no_fake_alpha_and_hard_cash_risk_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run(make_prices(folder),architecture="risknet",
                         return_head_strength=0,no_trade_band=.05)
        self.assertEqual(result["model_family"],RISKNET_NAME)
        self.assertEqual(result["method"],"atlaspm_original_risknet_v2_walk_forward")
        self.assertEqual(result["architecture_choice"],"risknet")
        self.assertFalse(result["return_prediction_head_trained"])
        self.assertEqual(
            result["final_trained_model_snapshot"]["targets"],
            ["log_annualized_forward_root_mean_square_return"],
        )
        self.assertIsNone(result["forecast_validation"]["return_mae_annualized"])
        self.assertIsNone(result["forecast_validation"]
                          ["return_forecast_better_than_zero_baseline"])
        self.assertGreater(
            result["forecast_validation"]["zero_return_baseline_mae_annualized"],0
        )
        self.assertGreater(
            result["forecast_validation"]["risk_mae_annualized"],0
        )
        self.assertGreater(len(result["rebalances"]),1)
        self.assertEqual(set(result["performance"]),{
            "atlas_native","naive_trailing_vol_risk","equal_weight_buy_and_hold"
        })
        self.assertEqual(result["policy"]["no_trade_band_l1_account_weight"],.05)
        self.assertFalse(result["live_execution_enabled"])
        self.assertFalse(result["model_validated_for_live_trading"])
        self.assertFalse(result["ready_for_institutional_risk_gate"])
        self.assertFalse(result["IBKR_financing_debt_separately_verified"])
        for rebalance in result["rebalances"]:
            self.assertEqual(rebalance["trained_model_architecture"],"risknet")
            self.assertEqual(rebalance["training_last_label_end_date"],
                             rebalance["signal_date"])
            self.assertLess(rebalance["signal_date"],rebalance["execution_date"])
            self.assertLess(rebalance["execution_date"],
                            rebalance["first_held_return_date"])
            self.assertTrue(all(v is None for v in
                                rebalance["annualized_return_forecasts"].values()))
            self.assertAlmostEqual(rebalance["return_confidence_exposure_cap"],1)
            allocation=rebalance["atlas_native_allocation"]
            self.assertLessEqual(allocation["risky_gross"],1)
            self.assertGreaterEqual(allocation["cash_weight"],0)
            self.assertAlmostEqual(
                sum(allocation["account_weight_targets"].values())+
                allocation["cash_weight"],1
            )
            self.assertFalse(rebalance["is_real_or_paper_broker_order"])
        for name,curve in result["equity_curves"].items():
            for row in curve:
                self.assertAlmostEqual(
                    row["cash_equity_index_units"]+
                    row["gross_holdings_equity_index_units"],
                    row["equity_index"],
                )
                self.assertGreaterEqual(row["cash_equity_index_units"],0)

    def test_risknet_future_last_price_is_not_available_to_earlier_decisions(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            original = run(
                make_prices(a), architecture="risknet",return_head_strength=0,
            )
            future = run(
                make_prices(b,future_change=True),
                architecture="risknet",return_head_strength=0,
            )
        self.assertEqual(original["rebalances"],future["rebalances"])
        self.assertNotEqual(
            original["performance"]["atlas_native"]["ending_equity_index"],
            future["performance"]["atlas_native"]["ending_equity_index"],
        )

    def test_hard_all_cash_gate_still_wins_and_cannot_be_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            result=run(make_prices(folder),architecture="risknet",
                       return_head_strength=0,maximum_uniform_stress_loss=0)
        self.assertTrue(all(
            step["atlas_native_allocation"]["risky_gross"]==0
            for step in result["rebalances"]
        ))
        self.assertAlmostEqual(
            result["performance"]["atlas_native"]["ending_equity_index"],1
        )

    def test_invalid_risk_only_configuration_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=make_prices(folder)
            for bad in (
                {"architecture":"risknet","return_head_strength":1},
                {"architecture":"risknet","return_head_strength":.01},
                {"architecture":"unknown","return_head_strength":0},
                {"architecture":"risknet","return_head_strength":0,
                 "maximum_gross_exposure":4},
            ):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    run(path,**bad)
        returns=synthetic_returns()
        with self.assertRaises(ValueError):
            predict_atlas_risknet({"model":"wrong"},[0.1]*11)


if __name__=="__main__":
    unittest.main()
