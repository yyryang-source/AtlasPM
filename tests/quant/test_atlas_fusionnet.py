"""Atlas FusionNet v0.7.0: scratch multi-scale risk heads, no hindsight, 1x risk."""

import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

from atlaspm.atlas_fusion_allocator import (
    _project_capped_simplex, _shrunken_correlation, allocate_fusion_sleeve,
)
from atlaspm.atlas_fusionnet import (
    FUSION_NAME, fit_atlas_fusionnet, fusion_feature_names,
    fusion_feature_vector, predict_atlas_fusionnet,
)
from atlaspm.atlas_risknet import RISKNET_NAME
from .test_atlas_regimenet import make_prices, run, synthetic_returns

HAS_NUMPY=importlib.util.find_spec("numpy") is not None
ROOT=Path(__file__).resolve().parents[2]


class ResearchIntegrityTests(unittest.TestCase):
    def test_research_register_has_at_least_20_named_sources(self):
        report=(ROOT/"docs"/"quant"/"MODEL_RESEARCH_ATLAS_30.md").read_text(encoding="utf-8")
        entries=[line for line in report.splitlines()
                 if line.startswith("| ") and len(line)>5
                 and line[2:4].isdigit() and "https://" in line]
        self.assertEqual(len(entries),30)
        self.assertIn("not",report.lower())
        self.assertIn("NOT a claim",report)

    def test_capped_simplex_feasibility_and_invalid_input(self):
        a=_project_capped_simplex([.90,.08,.02],.7)
        self.assertAlmostEqual(sum(a),1)
        self.assertLessEqual(max(a),.7+1e-10)
        self.assertGreaterEqual(min(a),-1e-10)
        with self.assertRaises(ValueError):
            _project_capped_simplex([0.2,0.3,0.5],.2)
        with self.assertRaises(ValueError):
            _project_capped_simplex([float("nan"),.2,.3],.7)

    def test_correlation_samples_shrunk_and_past_only(self):
        returns=[
            [math.sin(t*.3)*.006,math.sin(t*.3)*.006,
             -math.sin(t*.3)*.004] for t in range(25)
        ]
        cor=_shrunken_correlation(returns,3)
        self.assertAlmostEqual(cor[0][0],1)
        self.assertAlmostEqual(cor[0][1],.65)
        self.assertAlmostEqual(cor[0][2],-.65)
        self.assertAlmostEqual(cor[1][0],cor[0][1])
        sleeve=allocate_fusion_sleeve(
            ["AAA","BBB","CCC"],[.16,.16,.06],
            [.11,.11,.03],returns,max_weight=.7,
        )
        self.assertAlmostEqual(sum(sleeve["weights"].values()),1)
        self.assertLessEqual(max(sleeve["weights"].values()),.7+1e-10)
        self.assertFalse(sleeve["trained_on_future_returns"])
        self.assertEqual(sleeve["correlation_shrinkage_fixed"],.35)


@unittest.skipUnless(HAS_NUMPY,"Install optional Atlas NumPy native dependency")
class OriginalFusionTrainingTests(unittest.TestCase):
    def test_two_independently_trained_risk_heads_not_third_party_models(self):
        returns=synthetic_returns()
        fitted=fit_atlas_fusionnet(
            returns,100,20,5,60,train_window=70,epochs=20
        )
        self.assertEqual(fitted["model"],FUSION_NAME)
        self.assertNotEqual(fitted["model"],RISKNET_NAME)
        self.assertFalse(fitted["return_prediction_head_exists"])
        self.assertEqual(len(fitted["targets"]),2)
        self.assertTrue(fitted["trained_from_scratch"])
        self.assertFalse(fitted["pretrained_weights_used"])
        self.assertFalse(fitted["is_any_original_published_model_implementation"])
        self.assertLess(fitted["final_data_loss"],fitted["initial_data_loss"])
        self.assertEqual(fitted["label_fully_observed_through_price_index"],100)
        self.assertEqual(len(fitted["feature_names"]),
                         len(fitted["feature_means"]))
        self.assertEqual(len(fitted["expert_bias"][0]),2)
        v=predict_atlas_fusionnet(
            fitted,fusion_feature_vector(returns,100,1,20)
        )
        self.assertIsNone(v["annualized_arithmetic_return_forecast"])
        self.assertGreater(v["annualized_rms_risk_forecast"],0)
        self.assertGreater(v["annualized_downside_rms_risk_forecast"],0)
        self.assertLessEqual(
            v["annualized_downside_rms_risk_forecast"],
            v["annualized_rms_risk_forecast"]+1e-12,
        )
        self.assertAlmostEqual(sum(v["latent_expert_probabilities"]),1)

    def test_future_unobserved_returns_can_never_change_weights_or_features(self):
        returns=synthetic_returns()
        changed=[r[:] for r in returns]
        for r in changed:
            r[100:]=[.10]*len(r[100:])
        a=fit_atlas_fusionnet(returns,100,20,5,60,train_window=70,epochs=20)
        b=fit_atlas_fusionnet(changed,100,20,5,60,train_window=70,epochs=20)
        self.assertEqual(a,b)
        self.assertEqual(
            fusion_feature_vector(returns,100,0,20),
            fusion_feature_vector(changed,100,0,20)
        )
        self.assertEqual(
            predict_atlas_fusionnet(
                a,fusion_feature_vector(returns,100,0,20)),
            predict_atlas_fusionnet(
                b,fusion_feature_vector(changed,100,0,20)),
        )

    def test_end_to_end_native_fusion_backtest_is_safe_and_reports_downside(self):
        with tempfile.TemporaryDirectory() as folder:
            a=run(make_prices(folder),architecture="fusion",
                  return_head_strength=0,no_trade_band=.05)
        self.assertEqual(a["model_family"],FUSION_NAME)
        self.assertEqual(a["architecture_choice"],"fusion")
        self.assertTrue(a["downside_risk_head_trained"])
        self.assertFalse(a["return_prediction_head_trained"])
        self.assertIsNone(a["forecast_validation"]["return_mae_annualized"])
        self.assertIsNotNone(a["forecast_validation"]["downside_rms_mae_annualized"])
        self.assertEqual(
            set(a["performance"]),
            {"atlas_native","naive_trailing_vol_risk","equal_weight_buy_and_hold"}
        )
        self.assertGreater(len(a["rebalances"]),1)
        for step in a["rebalances"]:
            self.assertEqual(step["trained_model_architecture"],"fusion")
            self.assertEqual(step["training_last_label_end_date"],
                             step["signal_date"])
            self.assertLess(step["signal_date"],step["execution_date"])
            self.assertLess(step["execution_date"],step["first_held_return_date"])
            self.assertTrue(all(
                x is None for x in step["annualized_return_forecasts"].values()
            ))
            self.assertGreater(min(
                step["annualized_downside_risk_forecasts"].values()
            ),0)
            weights=step["atlas_native_allocation"]["risky_sleeve_weights"]
            self.assertAlmostEqual(sum(weights.values()),1)
            self.assertLessEqual(max(weights.values()),.7+1e-10)
            self.assertGreaterEqual(
                step["atlas_native_allocation"]["cash_weight"],0
            )
            self.assertLessEqual(
                step["atlas_native_allocation"]["risky_gross"],1
            )
            self.assertFalse(step["is_real_or_paper_broker_order"])
        for curve in a["equity_curves"].values():
            for p in curve:
                self.assertAlmostEqual(
                    p["gross_holdings_equity_index_units"]+
                    p["cash_equity_index_units"],p["equity_index"],
                )
                self.assertGreaterEqual(p["cash_equity_index_units"],0)
        self.assertFalse(a["live_execution_enabled"])
        self.assertFalse(a["ready_for_institutional_risk_gate"])
        self.assertFalse(a["IBKR_financing_debt_separately_verified"])

    def test_fusion_model_cannot_bypass_hard_all_cash_stress_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            result=run(make_prices(folder),architecture="fusion",
                       return_head_strength=0,maximum_uniform_stress_loss=0)
        self.assertTrue(all(
            s["atlas_native_allocation"]["risky_gross"]==0
            for s in result["rebalances"]
        ))
        self.assertAlmostEqual(
            result["performance"]["atlas_native"]["ending_equity_index"],1,
        )

    def test_fusion_last_unknown_price_cannot_change_prior_decisions(self):
        with tempfile.TemporaryDirectory() as x, tempfile.TemporaryDirectory() as y:
            original=run(make_prices(x),architecture="fusion",
                         return_head_strength=0)
            shifted=run(make_prices(y,True),architecture="fusion",
                        return_head_strength=0)
        self.assertEqual(original["rebalances"],shifted["rebalances"])
        self.assertNotEqual(
            original["performance"]["atlas_native"]["ending_equity_index"],
            shifted["performance"]["atlas_native"]["ending_equity_index"],
        )

    def test_invalid_architecture_and_margin_input_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=make_prices(folder)
            for kwargs in (
                {"architecture":"fusion","return_head_strength":1},
                {"architecture":"fusion","return_head_strength":.01},
                {"architecture":"fusion","return_head_strength":0,
                 "maximum_gross_exposure":4},
                {"architecture":"fusion","return_head_strength":0,
                 "maximum_name_weight":.2},
            ):
                with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                    run(path,**kwargs)


if __name__=="__main__":
    unittest.main()
