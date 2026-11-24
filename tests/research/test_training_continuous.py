"""Synthetic as-of and chronological replay integration tests."""
from copy import deepcopy
from importlib.util import find_spec
from unittest.mock import patch
import unittest

from research.test_training_autonomous import fixture, local_workspace, run
from atlaspm.research.training_engine import train
from atlaspm.research.training_continuous import current_forecasts,run_continuous


@unittest.skipUnless(find_spec("numpy"),"numpy required")
class AsOfTests(unittest.TestCase):
    def test_current_rows_unknown_and_match_later_walkforward(self):
        panel=fixture(95)
        seen={**panel,"sessions":panel["sessions"][:82]}
        next_day=panel["sessions"][82]["date"]
        opts=dict(horizon=1,minimum_train_sessions=10,test_sessions=5)
        a=train(seen,**opts,next_session=next_day)
        b=train(panel,**opts)
        day=seen["sessions"][-1]["date"]
        for name,rows in a["predictions"].items():
            current=[r for r in rows if r["date"]==day]
            self.assertEqual(len(current),2)
            self.assertTrue(all(r["actual"] is None and r["label_end_date"]==next_day for r in current))
            later=[r for r in b["predictions"][name] if r["date"]==day]
            for x,y in zip(current,later):
                self.assertAlmostEqual(x["prediction"],y["prediction"],places=12)
        self.assertFalse(a["live_execution_enabled"])
        self.assertFalse(a["approved_for_model_training"])

    def test_next_date_is_not_a_label_or_arbitrary_horizon(self):
        panel=fixture()
        for day,horizon in ((panel["sessions"][-1]["date"],1),("2030-01-01",5)):
            with self.assertRaisesRegex(ValueError,"INVALID_AS_OF_NEXT_SESSION"):
                train(panel,horizon=horizon,next_session=day)

    def test_registry_forecast_rejects_changed_frozen_prefix(self):
        with local_workspace() as path:
            study=run(fixture(),path)
            observed=fixture(115)
            day=fixture(116)["sessions"][-1]["date"]
            a=current_forecasts(observed,registry_path=path,run_id=study["run_id"],next_session=day)
            self.assertEqual(a["model"],"ridge")
            altered=deepcopy(observed)
            altered["sessions"][5]["assets"][0]["close"]*=2
            with self.assertRaisesRegex(ValueError,"FROZEN_RESEARCH_DATASET_MISMATCH"):
                current_forecasts(altered,registry_path=path,run_id=study["run_id"],next_session=day)
            self.assertTrue(all(r["actual"] is None for r in a["streams"]["ridge"] if r["date"]==a["date"]))


def opening():
    return dict(account_id="SYN-CONTINUOUS",account_type="cash",settled_cash=50000.,
                margin_debt=0.,positions=[dict(symbol=s,sector="Synthetic",shares=100,mark_price=100.)
                                          for s in ("AAA","BBB")])


LIMITS=dict(max_gross_exposure=.7,min_cash_fraction=.1,max_name_weight=.55,
            max_turnover=2.,min_trade_notional=0.,max_single_order_fraction=1.,
            max_annual_volatility=1.,max_stress_loss_fraction=.5)


@unittest.skipUnless(find_spec("scipy"),"scipy required")
class ContinuousTests(unittest.TestCase):
    def test_real_synthetic_oms_matures_and_account_advances(self):
        with local_workspace() as path:
            study=run(fixture(),path)
            result=run_continuous(fixture(125),registry_path=path,run_id=study["run_id"],
                        account=opening(),constraints=LIMITS,paper={"fee_per_order":0},max_steps=5)
            self.assertEqual(len(result["steps"]),6)
            completed=[r for r in result["steps"] if r["decision"]=="SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE"]
            self.assertTrue(completed,result["steps"])
            self.assertEqual(sum(r["matured_feedback"] is not None for r in result["steps"]),len(completed))
            for i,r in enumerate(result["steps"][:-1]):
                if r["decision"]=="SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE":
                    self.assertEqual(result["steps"][i+1]["matured_feedback"]["decision_id"],r["paper_decision_id"])
                    self.assertLess(r["signal_date"],r["date"])
            self.assertEqual(result["steps"][-1]["decision"],"MARK_ONLY_FINAL_SESSION")
            self.assertFalse(result["broker_order_submission_enabled"])
            self.assertEqual(result["unsettled_receivable"],0.)
            with self.assertRaisesRegex(ValueError,"CONTINUOUS_ACCOUNT_ALREADY_CONSUMED"):
                run_continuous(fixture(125),registry_path=path,run_id=study["run_id"],
                    account=opening(),constraints=LIMITS,max_steps=5)

    def test_future_prices_do_not_change_first_execution(self):
        panel=fixture(125)
        with local_workspace() as path:
            study=run(fixture(),path)
            a=run_continuous(panel,registry_path=path,run_id=study["run_id"],
                account=opening(),constraints=LIMITS,paper={"fee_per_order":0},max_steps=4)
        for session in panel["sessions"][113:]:
            for asset in session["assets"]:
                asset["close"]*=.8
        with local_workspace() as path:
            study=run(fixture(),path)
            b=run_continuous(panel,registry_path=path,run_id=study["run_id"],
                account=opening(),constraints=LIMITS,paper={"fee_per_order":0},max_steps=4)
        self.assertEqual(a["steps"][0],b["steps"][0])

    def test_real_panel_and_live_permission_refused(self):
        with local_workspace() as path:
            study=run(fixture(),path)
            panel=fixture(125)
            panel["live_execution_enabled"]=True
            with self.assertRaisesRegex(ValueError,"PERMISSION_OVERRIDE"):
                run_continuous(panel,registry_path=path,run_id=study["run_id"],
                    account=opening(),constraints=LIMITS)
