"""Atlas native v0.6.1 cost-aware no-trade and dual-head ablation tests."""

import importlib.util
import math
import tempfile
import unittest

from atlaspm.atlas_execution_policy import assess_native_no_trade
from atlaspm.ml_dynamic_rebalance import _select_allocation
from test_atlas_regimenet import make_prices, run

NAMES = ["AAA", "BBB", "CCC"]
RISKS = [.07, .08, .09]
TRAILING = [[.0001, -.002, .001]] * 20
GUARDS = {
    "maximum_name_weight": .7,
    "target_annual_volatility": .18,
    "maximum_gross_exposure": 1.0,
    "uniform_stress_decline": .3,
    "maximum_uniform_stress_loss": .25,
    "maximum_daily_tail_loss": .04,
    "account_drawdown_trigger": .10,
    "drawdown_exposure_cap": .25,
}


def target(*, drawdown=0):
    return _select_allocation(
        NAMES, RISKS, TRAILING,
        account_drawdown=drawdown,
        sleeve_weights=dict(zip(NAMES, [.4, .3, .3])),
        **GUARDS,
    )


def check(holdings, *, proposal=None, band=.05, drawdown=0, risks=None):
    allocation = proposal or target()
    equity=1.0
    return assess_native_no_trade(
        symbols=NAMES,
        existing_holdings=holdings,
        existing_cash=equity-sum(holdings),
        equity=equity,
        proposed_allocation=allocation,
        risk_forecasts=risks or RISKS,
        trailing_daily_returns=TRAILING,
        no_trade_band=band,
        name_weight_cap=.7,
        risk_policy={
            "target_annual_volatility": GUARDS["target_annual_volatility"],
            "maximum_gross_exposure": GUARDS["maximum_gross_exposure"],
            "uniform_stress_decline": GUARDS["uniform_stress_decline"],
            "maximum_uniform_stress_loss": GUARDS["maximum_uniform_stress_loss"],
            "maximum_daily_tail_loss": GUARDS["maximum_daily_tail_loss"],
            "account_drawdown_trigger": GUARDS["account_drawdown_trigger"],
            "drawdown_exposure_cap": GUARDS["drawdown_exposure_cap"],
        },
        account_drawdown=drawdown,
    )


class OriginalNoTradeRiskGuardTests(unittest.TestCase):
    def test_skip_when_small_gap_and_current_portfolio_is_safe(self):
        proposal=target()
        existing = [proposal["account_weight_targets"][s] for s in NAMES]
        result=check(existing,proposal=proposal)
        self.assertTrue(result["skipped"])
        self.assertTrue(result["risk_eligibility_rechecked"])
        self.assertEqual(result["reason"],"NO_TRADE_BAND_AND_RISK_CHECK_PASSED")
        self.assertAlmostEqual(result["account_weight_l1_gap"],0)

    def test_initial_entry_and_disabled_band_still_trade(self):
        self.assertFalse(check([0,0,0])["skipped"])
        proposed=target()
        holdings=[proposed["account_weight_targets"][s] for s in NAMES]
        result=check(holdings,band=0)
        self.assertFalse(result["skipped"])
        self.assertEqual(result["reason"],"NO_TRADE_BAND_DISABLED")

    def test_must_rebalance_actual_holdings_over_stress_cap_even_if_small_gap(self):
        result=check([.85*.4,.85*.3,.85*.3],band=.05)
        self.assertFalse(result["skipped"])
        self.assertTrue(result["risk_eligibility_rechecked"])
        self.assertEqual(
            result["reason"],"CURRENT_HOLDINGS_BREACH_UPDATED_RISK_BUDGET"
        )

    def test_must_rebalance_concentration_breach(self):
        proposal=_select_allocation(
            NAMES, RISKS, TRAILING,
            account_drawdown=0, sleeve_weights=dict(zip(NAMES,[.68,.16,.16])),
            **GUARDS,
        )
        current=[.83333*.71,.83333*.145,.83333*.145]
        result=check(current,proposal=proposal,band=.2)
        self.assertFalse(result["skipped"])
        self.assertEqual(result["reason"],"DRIFTED_CONCENTRATION_EXCEEDS_CAP")

    def test_invalid_band_and_nonreconciled_balance_rejected(self):
        with self.assertRaises(ValueError):
            check([0,0,0], band=1)
        with self.assertRaises(ValueError):
            assess_native_no_trade(
                symbols=NAMES, existing_holdings=[.2,.2,.2],
                existing_cash=1.0,equity=1.0,
                proposed_allocation=target(),
                risk_forecasts=RISKS,
                trailing_daily_returns=TRAILING,
                no_trade_band=.1,
                name_weight_cap=.7,
                risk_policy={},
                account_drawdown=0,
            )


@unittest.skipUnless(
    importlib.util.find_spec("numpy") is not None,
    "Atlas native ablation tests require optional numpy",
)
class OriginalReturnHeadAblationTests(unittest.TestCase):
    def test_zero_return_head_does_not_affect_portfolio_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            prices=make_prices(directory)
            report=run(prices,return_head_strength=0)
        self.assertEqual(report["policy"]["return_head_strength"],0)
        for step in report["rebalances"]:
            risks=step["annualized_risk_forecasts"]
            expected=[1/max(risks[k],.03) for k in NAMES]
            from atlaspm.atlas_regimenet import _capped_native_weights
            sleeve = _capped_native_weights(expected,.7)
            for k,value in zip(NAMES,sleeve):
                self.assertAlmostEqual(
                    step["atlas_native_allocation"]["risky_sleeve_weights"][k],
                    value,
                )
            self.assertEqual(step["return_confidence_exposure_cap"],1)
        self.assertGreater(
            report["forecast_validation"]["zero_return_baseline_mae_annualized"],
            0,
        )
        self.assertGreater(
            report["forecast_validation"]["naive_trailing_risk_mae_annualized"],
            0,
        )
        self.assertFalse(report["live_execution_enabled"])

    def test_same_no_trade_band_applies_to_native_and_traditional(self):
        with tempfile.TemporaryDirectory() as directory:
            prices=make_prices(directory)
            base=run(prices,no_trade_band=0,return_head_strength=1)
            altered=run(prices,no_trade_band=.5,return_head_strength=1)
        self.assertEqual(altered["policy"]["no_trade_band_l1_account_weight"],.5)
        for label in ("atlas_native","naive_trailing_vol_risk"):
            self.assertGreater(
                altered["execution_summary"][label]["skipped_rebalance_decisions"],
                0,
            )
            self.assertLess(
                altered["performance"][label]["rebalance_count"],
                base["performance"][label]["rebalance_count"],
            )
            self.assertTrue(all(
                x["cash_equity_index_units"] >= -1e-10
                for x in altered["equity_curves"][label]
            ))
            self.assertTrue(all(
                x["gross_holdings_equity_index_units"]
                <= x["equity_index"]+1e-8
                for x in altered["equity_curves"][label]
            ))
        self.assertTrue(any(
            x["native_execution_policy"]["skipped"]
            for x in altered["rebalances"]
        ))

    def test_parameter_validation_rejects_overleverage_and_unbounded_ablations(self):
        with tempfile.TemporaryDirectory() as directory:
            path=make_prices(directory)
            for bad in (
                {"return_head_strength": -0.01},
                {"return_head_strength": 1.01},
                {"no_trade_band": .51},
                {"no_trade_band": float("nan")},
                {"no_trade_band": True},
                {"maximum_gross_exposure": 2},
            ):
                with self.subTest(bad=bad),self.assertRaises(ValueError):
                    run(path,**bad)


if __name__ == "__main__":
    unittest.main()
