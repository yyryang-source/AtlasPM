import unittest
from copy import deepcopy
from datetime import date,timedelta
from importlib.util import find_spec
from atlaspm.research.training_dataset import build_panel,purged_folds
from atlaspm.research.training_engine import train


def fixture():
    rows=[]
    for i in range(45):
        d=str(date(2020,1,2)+timedelta(days=i))
        t=d+"T16:00:00-05:00"
        assets=[dict(security_id="SYN:"+str(j),source="synthetic",observed_at=t,available_at=t,
                     close=100*(1+.001*j)**i,features={"momentum":j,"variation":i%3})
                for j in range(1,5)]
        rows.append(dict(date=d,close_at=t,assets=assets))
    return dict(mode="synthetic",synthetic_fixture=True,feature_names=["momentum","variation"],sessions=rows)


class PanelTests(unittest.TestCase):
    def test_horizons(self):
        for h in (1,5,10,21):
            p=build_panel(fixture(),h)
            self.assertEqual(len(p["samples"]),(45-h)*4)
            self.assertEqual(p["samples"][0]["label_end_index"],h)
            self.assertAlmostEqual(p["samples"][0]["y"],1.001**h-1)

    def test_purge(self):
        p=build_panel(fixture(),5)
        for f in purged_folds(p,minimum_train_sessions=10,test_sessions=5,embargo_sessions=2):
            self.assertLess(f["latest_training_label_end"],f["test_start_index"]-2)

    def test_future_feature(self):
        p=fixture()
        p["sessions"][0]["assets"][0]["available_at"]=p["sessions"][1]["close_at"]
        with self.assertRaisesRegex(ValueError,"FEATURE_LOOKAHEAD"):
            build_panel(p,5)

    def test_missing_path(self):
        p=fixture()
        del p["sessions"][2]["assets"][0]
        out=build_panel(p,5)
        self.assertGreater(out["missing_label_paths"],0)
        self.assertFalse(any(r["security_id"]=="SYN:1" and r["signal_index"]==0 for r in out["samples"]))

    def test_real_data_blocked(self):
        p=fixture()
        p["mode"]="historical"
        with self.assertRaises(ValueError):
            build_panel(p,5)


@unittest.skipUnless(find_spec("numpy"),"portfolio extra required")
class TrainTests(unittest.TestCase):
    def test_prediction_quality_and_permissions(self):
        out=train(fixture(),minimum_train_sessions=10)
        self.assertGreater(out["metrics"]["ridge"]["mean_daily_rank_ic"],.9)
        self.assertLess(out["metrics"]["ridge"]["mae"],out["metrics"]["historical_mean"]["mae"])
        self.assertFalse(out["approved_for_model_training"])
        self.assertFalse(out["model_registry_admission"])

    def test_future_prices_do_not_change_earlier_predictions(self):
        p=fixture()
        a=train(p,minimum_train_sessions=10)
        for row in p["sessions"][35:]:
            for asset in row["assets"]:
                asset["close"]*=2
        b=train(p,minimum_train_sessions=10)
        self.assertEqual(a["folds"][0],b["folds"][0])
        self.assertEqual(a["predictions"]["ridge"][:20],b["predictions"]["ridge"][:20])
