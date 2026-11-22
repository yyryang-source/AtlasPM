"""AtlasPM original dual-head RegimeNet training and historical safety tests."""

import csv
import importlib.util
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.atlas_regimenet import (
    MODEL_NAME, _capped_native_weights, fit_atlas_regimenet,
    native_feature_vector, predict_atlas_regimenet, backtest_atlas_native,
)
from atlaspm.ml_dynamic_rebalance import _select_allocation

ROOT=Path(__file__).resolve().parents[2]
HAVE_NUMPY=importlib.util.find_spec("numpy") is not None


def synthetic_returns():
    return [
        [
            .0004*(j+1)
            + (.007 if i < 80 else .014)*(1+.2*j)
            *math.sin(i*.39+j*.8)
            for i in range(160)
        ] for j in range(3)
    ]


def make_prices(folder: str, future_change: bool = False) -> Path:
    path=Path(folder)/"private_prices.csv"
    levels=[100.0,96.0,84.0]
    with path.open("w",encoding="utf-8",newline="") as handle:
        w=csv.writer(handle)
        w.writerow(["date","symbol","adjusted_close"])
        for i in range(140):
            for j,symbol in enumerate(["AAA","BBB","CCC"]):
                if i:
                    levels[j] *= 1 + .0003*(j+1) + (
                        .006 if i < 65 else .018
                    )*(1+.12*j)*math.sin(i*.3+j*.6)
                price=levels[j]
                if future_change and i==139 and j==2:
                    price*=1.20
                w.writerow([
                    (date(2022,1,1)+timedelta(days=i)).isoformat(),
                    symbol, f"{price:.12f}"
                ])
    return path


def run(path:Path, **kwargs):
    settings=dict(
        feature_days=20, forecast_days=5,
        minimum_training_samples=60,
        training_window=70, sample_stride=2,
        training_epochs=15, rebalance_days=10,
    )
    settings.update(kwargs)
    return backtest_atlas_native(path,**settings)


@unittest.skipUnless(HAVE_NUMPY,"Install optional NumPy: pip install -e '.[native]'")
class OriginalAtlasModelTests(unittest.TestCase):
    def test_fit_has_real_learned_gating_experts_and_two_prediction_heads(self):
        prices=synthetic_returns()
        model=fit_atlas_regimenet(
            prices, signal_index=100, lookback=20,
            horizon=5, minimum_samples=60,
            train_window=70, sample_stride=2, epochs=20,
        )
        self.assertEqual(model["model"],MODEL_NAME)
        self.assertTrue(model["trained_from_scratch"])
        self.assertFalse(model["pretrained_weights_used"])
        self.assertFalse(model["model_validated_for_live_trading"])
        self.assertGreater(model["initial_data_loss"],model["final_data_loss"])
        self.assertEqual(model["label_fully_observed_through_price_index"],100)
        self.assertLessEqual(model["training_time_last_signal_index"],95)
        self.assertEqual(len(model["gate_weights"][0]),3)
        self.assertEqual(len(model["expert_weights"]),3)
        self.assertEqual(len(model["expert_bias"][0]),2)
        out=predict_atlas_regimenet(
            model,native_feature_vector(prices,100,1,20)
        )
        self.assertTrue(math.isfinite(out["annualized_rms_risk_forecast"]))
        self.assertTrue(math.isfinite(out["annualized_arithmetic_return_forecast"]))
        self.assertEqual(len(out["latent_expert_probabilities"]),3)
        self.assertAlmostEqual(sum(out["latent_expert_probabilities"]),1)

    def test_train_and_predict_cannot_see_future_returns(self):
        original=synthetic_returns()
        changed=[v[:] for v in original]
        for row in changed:
            for i in range(100,len(row)):
                row[i]=.1
        a=fit_atlas_regimenet(original,100,20,5,60,train_window=70,epochs=15)
        b=fit_atlas_regimenet(changed,100,20,5,60,train_window=70,epochs=15)
        self.assertEqual(a,b)
        self.assertEqual(
            predict_atlas_regimenet(a,native_feature_vector(original,100,0,20)),
            predict_atlas_regimenet(b,native_feature_vector(changed,100,0,20)),
        )

    def test_custom_weights_must_still_pass_independent_hard_risk_constraints(self):
        w=_capped_native_weights([1,3,50],.7)
        self.assertAlmostEqual(sum(w),1)
        self.assertLessEqual(max(w),.7+1e-10)
        common=dict(
            maximum_name_weight=.7,
            target_annual_volatility=.15,
            maximum_gross_exposure=1,
            uniform_stress_decline=.30,
            maximum_uniform_stress_loss=.25,
            maximum_daily_tail_loss=.04,
            account_drawdown=0,
            account_drawdown_trigger=.10,
            drawdown_exposure_cap=.25,
        )
        allocation=_select_allocation(
            ["A","B","C"],[.25,.35,.4],
            [[.001,.002,.003]]*20,
            sleeve_weights=dict(zip(["A","B","C"],w)),
            additional_exposure_cap=.3,**common,
        )
        self.assertLessEqual(allocation["risky_gross"],.3)
        self.assertLessEqual(allocation["conservative_forecast_annual_risk"],.15+1e-9)
        self.assertLessEqual(max(allocation["risky_sleeve_weights"].values()),.7)
        with self.assertRaises(ValueError):
            _select_allocation(
                ["A","B","C"],[.25,.35,.4],[[0,0,0]]*20,
                sleeve_weights={"A":.8,"B":.1,"C":.1},
                **common,
            )
        with self.assertRaises(ValueError):
            _select_allocation(
                ["A","B","C"],[.25,.35,.4],[[0,0,0]]*20,
                sleeve_weights={"A":.4,"B":.3,"C":.3},
                additional_exposure_cap=1.1,**common,
            )

    def test_end_to_end_true_native_training_and_dynamic_cash_rebalancing(self):
        with tempfile.TemporaryDirectory() as folder:
            result=run(make_prices(folder))
        self.assertEqual(result["model_family"],MODEL_NAME)
        self.assertTrue(result["trained_from_scratch"])
        self.assertFalse(result["pretrained_weights_used"])
        self.assertFalse(result["live_execution_enabled"])
        self.assertFalse(result["model_validated_for_live_trading"])
        self.assertFalse(result["ready_for_institutional_risk_gate"])
        self.assertEqual(set(result["performance"]),{
            "atlas_native","naive_trailing_vol_risk","equal_weight_buy_and_hold"
        })
        self.assertGreater(len(result["rebalances"]),1)
        for decision in result["rebalances"]:
            self.assertLess(decision["training_last_eligible_signal_date"],
                            decision["signal_date"])
            self.assertEqual(
                decision["training_last_label_end_date"],
                decision["signal_date"],
            )
            self.assertLess(decision["signal_date"],decision["execution_date"])
            self.assertLess(decision["execution_date"],
                            decision["first_held_return_date"])
            self.assertGreater(
                decision["training_initial_loss"],decision["training_final_loss"]
            )
            allocation=decision["atlas_native_allocation"]
            self.assertLessEqual(allocation["risky_gross"],1)
            self.assertGreaterEqual(allocation["cash_weight"],-1e-12)
            self.assertAlmostEqual(sum(allocation["account_weight_targets"].values())
                                   +allocation["cash_weight"],1)
            self.assertFalse(decision["is_real_or_paper_broker_order"])
        for curve in result["equity_curves"].values():
            for point in curve:
                self.assertAlmostEqual(
                    point["cash_equity_index_units"]
                    +point["gross_holdings_equity_index_units"],
                    point["equity_index"],
                )

    def test_future_last_bar_does_not_rewrite_earlier_trades(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            orig=run(make_prices(a))
            future=run(make_prices(b,future_change=True))
        self.assertEqual(orig["rebalances"],future["rebalances"])
        self.assertNotEqual(
            orig["performance"]["atlas_native"]["ending_equity_index"],
            future["performance"]["atlas_native"]["ending_equity_index"],
        )

    def test_all_cash_hard_gate_never_allow_trained_model_override(self):
        with tempfile.TemporaryDirectory() as directory:
            report=run(
                make_prices(directory),maximum_uniform_stress_loss=0
            )
        self.assertTrue(all(
            step["atlas_native_allocation"]["risky_gross"]==0
            for step in report["rebalances"]
        ))
        self.assertAlmostEqual(
            report["performance"]["atlas_native"]["ending_equity_index"],1
        )

    def test_unsafe_or_infeasible_parameters_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=make_prices(directory)
            invalid=[
                {"maximum_gross_exposure":1.3},
                {"maximum_name_weight":.2},
                {"minimum_training_samples":5000},
                {"training_window":30},
                {"training_epochs":0},
                {"sample_stride":22},
                {"fee_bps":float("nan")},
                {"maximum_uniform_stress_loss":True},
            ]
            for args in invalid:
                with self.subTest(args=args),self.assertRaises(ValueError):
                    run(path,**args)


if __name__=="__main__":
    unittest.main()
