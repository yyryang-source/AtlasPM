import unittest
from importlib.util import find_spec
from atlaspm.research.training_dataset import build_panel
from atlaspm.research.training_engine import train
from atlaspm.research.training_ensemble import adaptive_ensemble
from .test_training_engine import fixture


class SequencePanelTests(unittest.TestCase):
    def test_trailing_sequence(self):
        p=build_panel(fixture(),5,3)
        row=p["prediction_samples"][0]
        self.assertEqual(row["signal_index"],2)
        self.assertEqual(row["sequence"],[[1.,0.],[1.,1.],[1.,2.]])

    def test_missing_future_label_does_not_remove_prediction(self):
        p=fixture()
        del p["sessions"][20]["assets"][0]
        panel=build_panel(p,5,3)
        row=next(r for r in panel["prediction_samples"] if r["signal_index"]==18 and r["security_id"]=="SYN:1")
        self.assertIsNone(row["y"])
        self.assertNotIn(row,panel["samples"])


class EnsembleTests(unittest.TestCase):
    def forecasts(self):
        from datetime import date,timedelta
        out={"good":[],"bad":[]}
        for i in range(10):
            day=date(2020,1,1)+timedelta(days=i)
            for name,pred in (("good",.1),("bad",-.1)):
                out[name].append(dict(date=str(day),security_id="SYN:A",
                                      label_end_date=str(day+timedelta(days=2)),
                                      actual=.1,prediction=pred))
        return out

    def test_maturity_and_weights(self):
        out=adaptive_ensemble(self.forecasts(),minimum_dates=1)
        self.assertEqual(out["weight_history"][2]["weights"]["good"],.5)
        self.assertGreater(out["weight_history"][3]["weights"]["good"],.99)

    def test_future_actual_invariance(self):
        p=self.forecasts()
        a=adaptive_ensemble(p,minimum_dates=1)
        for rows in p.values():
            rows[5]["actual"]=-.3
        b=adaptive_ensemble(p,minimum_dates=1)
        self.assertEqual(a["weight_history"][:8],b["weight_history"][:8])

    def test_alignment_refused(self):
        p=self.forecasts()
        p["good"].pop()
        with self.assertRaises(ValueError):
            adaptive_ensemble(p)


@unittest.skipUnless(find_spec("torch") and find_spec("numpy"),"research-deep extra required")
class DeepTests(unittest.TestCase):
    def test_three_models_and_ensemble(self):
        out=train(fixture(),minimum_train_sessions=10,test_sessions=15,
                  models=["ridge","lstm","gru","transformer"],sequence_length=3,
                  sequence_epochs=2,ensemble={"minimum_dates":1})
        self.assertEqual(set(out["metrics"]),{"ridge","lstm","gru","transformer","adaptive_ensemble"})
        self.assertTrue(out["ensemble_weight_history"])
        self.assertFalse(out["model_registry_admission"])

    def test_missing_future_label_keeps_prediction(self):
        p=fixture()
        a=train(p,minimum_train_sessions=10,test_sessions=10,models=["gru"],
                sequence_length=3,sequence_epochs=2)
        del p["sessions"][22]["assets"][0]
        b=train(p,minimum_train_sessions=10,test_sessions=10,models=["gru"],
                sequence_length=3,sequence_epochs=2)
        key=lambda r:r["security_id"]=="SYN:1" and r["date"]==p["sessions"][18]["date"]
        x=next(r for r in a["predictions"]["gru"] if key(r))
        y=next(r for r in b["predictions"]["gru"] if key(r))
        self.assertIsNone(y["actual"])
        self.assertAlmostEqual(x["prediction"],y["prediction"],places=6)
