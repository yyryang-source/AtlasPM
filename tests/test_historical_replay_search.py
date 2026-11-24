import unittest
from copy import deepcopy
from atlaspm.engine.replay_search import search
from test_historical_replay import fixture


class SearchTests(unittest.TestCase):
    def sample(self):
        p=fixture()
        from datetime import date,timedelta
        rows=[]
        for i in range(14):
            row=deepcopy(p["sessions"][0])
            d=str(date(2020,1,2)+timedelta(days=i))
            row["date"]=d
            row["open_at"]=d+"T09:30:00-05:00"
            row["close_at"]=d+"T16:00:00-05:00"
            row["open"]={"AAA":100+i*3+(i%2)*2,"BBB":100+i}
            row["close"]=dict(row["open"])
            for s in row["signals"]:
                for k in ("generated_at","available_at","data_available_at","expires_at"):
                    s[k]=row["close_at"]
            rows.append(row)
        p["sessions"]=rows
        return p

    def test_holdout_cannot_change_winner(self):
        p=self.sample()
        grid={"top_k":[1,2],"rebalance_sessions":[1,2]}
        a=search(p,grid,5,10)
        self.assertEqual(a["status"],"COMPLETE")
        for row in p["sessions"][10:]:
            row["open"]["AAA"]*=.7
            row["close"]["AAA"]*=.7
        b=search(p,grid,5,10)
        self.assertEqual(a["selected_parameters"],b["selected_parameters"])
        self.assertEqual(a["trials"],b["trials"])
        self.assertNotEqual(a["holdout"],b["holdout"])

    def test_trial_cap(self):
        with self.assertRaisesRegex(ValueError,"SEARCH_TRIAL_LIMIT"):
            search(self.sample(),{"top_k":list(range(129))},5,10)

    def test_disallowed_parameter(self):
        with self.assertRaises(ValueError):
            search(self.sample(),{"mode":["historical"]},5,10)

    def test_invalid_split(self):
        with self.assertRaises(ValueError):
            search(self.sample(),{"top_k":[1]},5,6)

    def test_no_valid_trial(self):
        out=search(self.sample(),{"top_k":[0]},5,10)
        self.assertEqual(out["status"],"NO_VALID_TRIAL")
        self.assertNotIn("holdout",out)
