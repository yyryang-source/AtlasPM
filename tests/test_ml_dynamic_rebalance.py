"""v0.5.0 tests for a genuine fitted ML risk model driving offline allocation."""

import csv
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.ml_dynamic_rebalance import (
    _features, _train_asset_model, _forecast, _select_allocation,
    backtest_ml_dynamic_rebalance,
)


def write_prices(folder: str, *, final_price_changed: bool = False,
                 n_days: int = 220) -> Path:
    path = Path(folder) / "training_prices.csv"
    prices = {"AAA":100.0, "BBB":120.0, "CCC":85.0}
    first = date(2022, 1, 1)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date","symbol","adjusted_close"])
        for i in range(n_days):
            for k, symbol in enumerate(prices):
                if i:
                    regime = .006 if i < 105 else .017
                    amplitude = regime * (1 + k*0.35)
                    momentum = .001 * (k+1) * (1 if i < 170 else -1)
                    daily = momentum + amplitude*math.sin(.83*i+.7*k)
                    prices[symbol] *= 1+daily
                close = prices[symbol]
                if final_price_changed and i == n_days-1 and symbol=="CCC":
                    close *= 1.13
                writer.writerow([
                    (first+timedelta(days=i)).isoformat(),symbol,
                    format(close, ".12f")
                ])
    return path


def research_test(path:Path, **kwargs):
    options = dict(
        feature_days=20, forecast_days=5,
        minimum_training_samples=30, rebalance_days=8,
    )
    options.update(kwargs)
    return backtest_ml_dynamic_rebalance(path, **options)


class SupervisedDynamicRebalanceTests(unittest.TestCase):
    def test_real_fitted_models_and_full_cash_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            data=research_test(write_prices(folder))
        self.assertTrue(data["is_trained_machine_learning_model"])
        self.assertEqual(data["model_family"],
                         "supervised_ridge_regression_log_forward_rms_risk")
        self.assertGreater(len(data["rebalances"]),10)
        self.assertEqual(set(data["final_trained_model_snapshot"]),{"AAA","BBB","CCC"})
        for symbol, model in data["final_trained_model_snapshot"].items():
            self.assertEqual(model["model"],"trained_regularized_log_future_rms_ridge")
            self.assertGreaterEqual(model["training_samples"],30)
            self.assertEqual(len(model["coefficients"]),5)
            self.assertTrue(all(math.isfinite(x) for x in model["coefficients"]))
        self.assertFalse(data["model_validated_for_live_trading"])
        self.assertFalse(data["live_execution_enabled"])
        self.assertFalse(data["ready_for_institutional_risk_gate"])
        self.assertFalse(data["IBKR_financing_debt_separately_verified"])
        for name,curve in data["equity_curves"].items():
            self.assertGreater(len(curve),0)
            for point in curve:
                self.assertAlmostEqual(
                    point["gross_holdings_equity_index_units"]
                    +point["cash_equity_index_units"],
                    point["equity_index"],places=9)
                self.assertGreaterEqual(point["cash_equity_index_units"],-1e-12)

    def test_ml_predicts_risk_not_equity_returns_and_dynamic_asset_reweighting(self):
        with tempfile.TemporaryDirectory() as folder:
            data=research_test(write_prices(folder),target_annual_volatility=.09)
        first=data["rebalances"][0]
        later=data["rebalances"][-1]
        self.assertNotEqual(first["ai_forecast_annual_rms"],later["ai_forecast_annual_rms"])
        self.assertNotEqual(first["ai_allocation"]["risky_sleeve_weights"],
                            later["ai_allocation"]["risky_sleeve_weights"])
        self.assertGreater(len(set(
            round(r["ai_allocation"]["risky_gross"],6)
            for r in data["rebalances"]
        )),1)
        for rebalance in data["rebalances"]:
            weights=rebalance["ai_allocation"]["account_weight_targets"]
            gross=rebalance["ai_allocation"]["risky_gross"]
            self.assertGreaterEqual(gross,0)
            self.assertLessEqual(gross,1)
            self.assertAlmostEqual(sum(weights.values())+
                                   rebalance["ai_allocation"]["cash_weight"],1)
            self.assertTrue(all(0 <= value <= 1 for value in weights.values()))
            self.assertLessEqual(max(
                rebalance["ai_allocation"]["risky_sleeve_weights"].values()
            ), .7+1e-10)

    def test_strict_signal_training_label_execution_date_order(self):
        with tempfile.TemporaryDirectory() as folder:
            data=research_test(write_prices(folder))
        for step in data["rebalances"]:
            self.assertLess(
                step["most_recent_training_feature_date"],step["signal_date"]
            )
            self.assertEqual(step["training_last_label_date"],step["signal_date"])
            self.assertLess(step["signal_date"],step["execution_date"])
            self.assertLess(step["execution_date"],step["first_held_return_date"])
            self.assertFalse(step["is_real_or_paper_broker_order"])
        self.assertGreaterEqual(
            data["rebalances"][-1]["training_sample_count_per_asset"],
            data["rebalances"][0]["training_sample_count_per_asset"]
        )

    def test_future_price_change_does_not_modify_earlier_ai_decisions(self):
        with tempfile.TemporaryDirectory() as folder1, tempfile.TemporaryDirectory() as folder2:
            base=research_test(write_prices(folder1))
            future=research_test(write_prices(folder2,final_price_changed=True))
        self.assertEqual(base["rebalances"][0]["ai_forecast_annual_rms"],
                         future["rebalances"][0]["ai_forecast_annual_rms"])
        self.assertEqual(base["rebalances"][0]["ai_allocation"],
                         future["rebalances"][0]["ai_allocation"])
        self.assertEqual(base["rebalances"][-1]["ai_allocation"],
                         future["rebalances"][-1]["ai_allocation"])
        self.assertNotEqual(
            base["performance"]["ai"]["ending_equity_index"],
            future["performance"]["ai"]["ending_equity_index"]
        )

    def test_oos_model_compared_with_naive_and_buy_hold(self):
        with tempfile.TemporaryDirectory() as folder:
            report=research_test(write_prices(folder))
        scores=report["forecast_validation"]
        self.assertGreater(scores["out_of_sample_asset_forecast_count"],0)
        self.assertGreaterEqual(scores["ml_mean_absolute_error_annual_vol"],0)
        self.assertGreaterEqual(scores["naive_mean_absolute_error_annual_vol"],0)
        self.assertIsInstance(scores["ml_outperforms_naive_on_observed_oos"],bool)
        self.assertEqual(
            set(report["performance"]),
            {"ai","naive_trailing_vol_risk","equal_weight_buy_and_hold"}
        )
        self.assertEqual(
            report["performance"]["equal_weight_buy_and_hold"]["rebalance_count"],1
        )

    def test_higher_assumed_trading_cost_reduces_net_value(self):
        with tempfile.TemporaryDirectory() as folder:
            path=write_prices(folder)
            zero=research_test(path,fee_bps=0,slippage_bps=0,
                               account_drawdown_trigger=.95)
            costly=research_test(path,fee_bps=30,slippage_bps=30,
                                 account_drawdown_trigger=.95)
        self.assertGreater(
            costly["performance"]["ai"]["total_cost_equity_index_units"],0
        )
        self.assertLess(
            costly["performance"]["ai"]["ending_equity_index"],
            zero["performance"]["ai"]["ending_equity_index"]
        )

    def test_explicit_zero_stress_loss_cap_all_cash(self):
        with tempfile.TemporaryDirectory() as folder:
            result=research_test(write_prices(folder),maximum_uniform_stress_loss=0)
        self.assertTrue(all(
            r["ai_allocation"]["risky_gross"]==0 for r in result["rebalances"]
        ))
        self.assertEqual(result["performance"]["ai"]["ending_equity_index"],1)
        self.assertEqual(result["performance"]["ai"]["total_cost_equity_index_units"],0)

    def test_drawdown_guard_applies_to_allocation(self):
        portfolio=_select_allocation(
            ["A","B"],[.20,.30],[[.0,.0] for _ in range(20)],
            maximum_name_weight=.7,target_annual_volatility=.5,
            maximum_gross_exposure=1,uniform_stress_decline=.3,
            maximum_uniform_stress_loss=.5,maximum_daily_tail_loss=.05,
            account_drawdown=-.15,account_drawdown_trigger=.1,
            drawdown_exposure_cap=.2,
        )
        self.assertAlmostEqual(portfolio["risky_gross"],.2)
        self.assertIn("current_equity_drawdown_guard",portfolio["binding_caps"])

    def test_ridge_fit_uses_only_labels_ending_by_signal(self):
        returns=[.01*math.sin(i*.21)+.0005 for i in range(100)]
        model=_train_asset_model(returns,signal_index=75,lookback=20,
                                 horizon=5,minimum_samples=20)
        self.assertEqual(model["label_fully_observed_through_price_index"],75)
        self.assertEqual(model["training_last_signal_index"],70)
        self.assertEqual(model["training_samples"],51)
        first=_forecast(model,_features(returns,75,20))
        modified=returns[:]
        modified[75:]=[.4]*len(modified[75:])
        unchanged=_train_asset_model(modified,signal_index=75,lookback=20,
                                     horizon=5,minimum_samples=20)
        self.assertEqual(model,unchanged)
        self.assertEqual(first,_forecast(unchanged,_features(modified,75,20)))

    def test_ridge_handles_constant_vol_regime_without_singular_fit(self):
        returns=[.01]*120
        model=_train_asset_model(returns,80,20,5,20)
        prediction=_forecast(model,_features(returns,80,20))
        self.assertTrue(math.isfinite(prediction))
        self.assertGreater(prediction,0)

    def test_invalid_or_infeasible_limits_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=write_prices(folder)
            cases=[
                {"maximum_gross_exposure":1.01},
                {"maximum_name_weight":.2},
                {"target_annual_volatility":0},
                {"feature_days":4},
                {"forecast_days":0},
                {"minimum_training_samples":19},
                {"rebalance_days":0},
                {"maximum_daily_tail_loss":-1},
                {"fee_bps":float("nan")},
                {"slippage_bps":-5},
                {"account_drawdown_trigger":0},
                {"uniform_stress_decline":1.1},
                {"maximum_uniform_stress_loss":True},
            ]
            for kwargs in cases:
                with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                    research_test(path,**kwargs)

    def test_too_short_history_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,"Not enough aligned prices"):
                research_test(write_prices(folder,n_days=55))


if __name__=="__main__":
    unittest.main()
