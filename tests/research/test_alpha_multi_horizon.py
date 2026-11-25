"""Actual synthetic multi-horizon fits and fail-closed evaluation."""
from copy import deepcopy
from datetime import date, timedelta
from importlib.util import find_spec
import unittest
from unittest.mock import patch

from atlaspm.research import alpha_multi_horizon as a
from .test_training_engine import fixture


def panel():
    p=fixture()
    original=p["sessions"]
    p["sessions"]=[]
    for i in range(100):
        s=deepcopy(original[i%len(original)])
        day=str(date(2020,1,2)+timedelta(days=i))
        stamp=day+"T16:00:00-05:00"
        s.update(date=day,close_at=stamp)
        for j,x in enumerate(s["assets"],1):
            x.update(close=100*(1+.001*j)**i,observed_at=stamp,available_at=stamp)
        p["sessions"].append(s)
    return p


def settings():
    return dict(holdout_start=60,config={"minimum_train_sessions":10,"test_sessions":4})


@unittest.skipUnless(find_spec("numpy"),"numpy extra required")
class MultiHorizonTests(unittest.TestCase):
    def test_all_winners_locked_before_full_fits(self):
        p=panel()
        with patch.object(a.tournament,"train",wraps=a.tournament.train) as train:
            r=a.research(p,**settings())
        self.assertEqual([len(c.args[0]["sessions"]) for c in train.call_args_list],
                         [60]*6+[100]*3)
        self.assertEqual(list(r["horizons"]),["1","5","21"])
        for h,report in r["horizons"].items():
            self.assertEqual(report["holdout_candidates_evaluated"],
                             [report["selection"]["selected_model"]])
            self.assertTrue(report["nonoverlapping_diagnostic"]["coverage_complete"])
            self.assertEqual(report["nonoverlapping_diagnostic"]["planned_nonoverlapping_dates"],
                             len(range(60,100-int(h),int(h))))
        self.assertTrue(r["all_nonoverlapping_cohorts_complete"])
        self.assertEqual(r["decision"],"NO_TRADE")
        self.assertEqual(r["signals"],[])
        for k in ("approved_for_model_training","approved_for_backtest",
                  "model_registry_admission","live_execution_enabled"):
            self.assertFalse(r[k])

    def test_holdout_changes_do_not_change_selection_lock(self):
        p=panel()
        r=a.research(p,**settings())
        for s in p["sessions"][60:]:
            for x in s["assets"]:
                x["close"]*=2
                x["features"]["momentum"]*=10
        other=a.research(p,**settings())
        self.assertEqual(r["selection_lock_sha256"],other["selection_lock_sha256"])
        self.assertNotEqual(r["dataset_sha256"],other["dataset_sha256"])
        for h in r["horizons"]:
            self.assertEqual(r["horizons"][h]["selection"],other["horizons"][h]["selection"])


class GuardTests(unittest.TestCase):
    def test_invalid_request_before_fit(self):
        p=panel()
        with patch.object(a.tournament,"train") as train:
            for data,kw in (
                ({**p,"mode":"historical"},settings()),
                ({**p,"live_execution_enabled":True},settings()),
                (p,{**settings(),"holdout_start":61}),
                (p,{**settings(),"horizons":[1,1]}),
                (p,{**settings(),"horizons":[1,True]}),
                (p,{**settings(),"candidates":[{"model":"ridge"}]*2}),
                (p,{**settings(),"config":{"horizon":1}}),
            ):
                with self.subTest(kw=kw),self.assertRaises(ValueError):
                    a.research(data,**kw)
            train.assert_not_called()

    def test_nonoverlap_fixed_dates_and_missing_cohort(self):
        dates=["2020-01-0"+str(i) for i in range(1,9)]
        expected={d:{"SYN:A","SYN:B"} for d in dates}
        rows=[]
        for i in (1,3,5):
            for sid in sorted(expected[dates[i]]):
                rows.append({"date":dates[i],"security_id":sid,
                             "label_end_date":dates[i+2],"prediction":.1,"actual":.2})
        rows.pop(2)  # incomplete middle fixed cohort
        r=a._holdout_diagnostics(rows,dates,expected,2,1)
        self.assertEqual(r["planned_nonoverlapping_dates"],3)
        self.assertEqual(r["complete_nonoverlapping_dates"],2)
        self.assertEqual(r["missing_or_incomplete_dates"],[dates[3]])
        self.assertFalse(r["coverage_complete"])
        rows[0]["label_end_date"]=dates[4]
        with self.assertRaisesRegex(ValueError,"LABEL_MATURITY"):
            a._holdout_diagnostics(rows,dates,expected,2,1)

    def test_budget_rejected_before_fit(self):
        with patch.object(a,"MAX_FOLD_FITS",1),patch.object(a.tournament,"train") as train:
            with self.assertRaisesRegex(ValueError,"FOLD_FIT_BUDGET"):
                a.research(panel(),**settings())
            train.assert_not_called()
