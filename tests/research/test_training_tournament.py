import unittest
from importlib.util import find_spec
from unittest.mock import patch

from atlaspm.research import training_tournament as t
from .test_training_engine import fixture


def settings():
    return dict(config={"minimum_train_sessions":10,"test_sessions":5},
                candidates=[{"model":"historical_mean"},{"model":"ridge","ridge_alpha":.1},
                            {"model":"ridge","ridge_alpha":10.}],
                holdout_start=31)


@unittest.skipUnless(find_spec("numpy"),"numpy extra required")
class TournamentTests(unittest.TestCase):
    def test_actual_model_selection_and_holdout_isolation(self):
        p=fixture()
        a=t.tournament(p,**settings())
        for session in p["sessions"][31:]:
            for asset in session["assets"]:
                asset["close"]*=2
        b=t.tournament(p,**settings())
        self.assertEqual(a["trials"],b["trials"])
        self.assertEqual(a["selected_config"],b["selected_config"])
        self.assertEqual(a["selected_model"],"ridge")
        self.assertEqual(a["trial_count"],3)
        self.assertTrue(all(r["date"]>=a["holdout_start"] for r in a["holdout_predictions"]))
        self.assertFalse(a["approved_for_model_training"])
        self.assertFalse(a["model_registry_admission"])
        self.assertFalse(a["live_execution_enabled"])

    def test_only_winner_receives_full_data(self):
        p=fixture()
        with patch.object(t,"train",wraps=t.train) as call:
            r=t.tournament(p,**settings())
        self.assertEqual(call.call_count,4)
        self.assertEqual([len(c.args[0]["sessions"]) for c in call.call_args_list],[31,31,31,45])
        self.assertEqual(call.call_args.kwargs["models"],[r["selected_model"]])

    def test_rank_metric(self):
        r=t.tournament(fixture(),**settings(),selection_metric="mean_daily_rank_ic")
        self.assertEqual(r["selected_model"],"ridge")
        self.assertIsNone(r["trials"][0]["selection_score"])


class GuardTests(unittest.TestCase):
    def test_reject_real_and_bad_candidates_before_fit(self):
        p=fixture()
        cases=[({**p,"mode":"historical"},settings()),
               (p,{**settings(),"candidates":[{"model":"ridge"}]*2}),
               (p,{**settings(),"candidates":[{"model":"ridge"},{"model":"lstm"}]}),
               (p,{**settings(),"candidates":[{"model":"ridge"},{"model":"random_forest","ridge_alpha":2}]}),
               (p,{**settings(),"holdout_start":32}),
               (p,{**settings(),"config":{"checkpoint_path":"local_data/checkpoint"}}),
               (p,{**settings(),"candidates":[{"model":"ridge"}]*33})]
        with patch.object(t,"train") as call:
            for data,kwargs in cases:
                with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                    t.tournament(data,**kwargs)
            call.assert_not_called()

    def test_mismatched_validation_universe_rejected(self):
        def fake(payload,**kwargs):
            name=kwargs["models"][0]
            return {"predictions":{name:[{"security_id":"SYN:"+name,"date":"2020-01-01",
                        "label_end_date":"2020-01-02","actual":.1}]},
                    "metrics":{name:{"mae":.1}},"dependency_versions":{}}
        with patch.object(t,"train",side_effect=fake) as call:
            with self.assertRaisesRegex(ValueError,"TOURNAMENT_VALIDATION_UNIVERSE_MISMATCH"):
                t.tournament(fixture(),**settings())
        self.assertEqual(call.call_count,2)
