import unittest
from datetime import date,timedelta
from atlaspm.research.training_monitor import monitor_predictions


def streams():
    out={"historical_mean":[],"ridge":[]}
    start=date(2020,1,1)
    for i in range(40):
        for j in range(3):
            for model,values in out.items():
                values.append(dict(date=str(start+timedelta(days=i)),security_id=f"SYN:{j}",
                    label_end_date=str(start+timedelta(days=i+2)),actual=.1,
                    prediction=.1 if model=="ridge" else .0))
    return out


class MonitoringTests(unittest.TestCase):
    def test_maturity_champion_and_calibration(self):
        result=monitor_predictions(streams(),minimum_dates=3,window_dates=10,coverage=.75)
        self.assertEqual(result["daily"][2]["matured_calibration_dates"],0)
        self.assertEqual(result["daily"][3]["matured_calibration_dates"],1)
        self.assertEqual(result["daily"][5]["champion"],"ridge")
        self.assertEqual(result["daily"][5]["models"]["ridge"]["interval_half_width"],0)
        self.assertFalse(result["automatic_promotion_enabled"])
        self.assertEqual(result["model_review"]["ridge"]["status"],"CANDIDATE_FOR_INDEPENDENT_REVIEW")
        self.assertFalse(result["live_execution_enabled"])

    def test_future_labels_do_not_change_earlier_monitor(self):
        p=streams()
        a=monitor_predictions(p,minimum_dates=3,window_dates=10,coverage=.75)
        for rows in p.values():
            rows[30]["actual"]=-.5
        b=monitor_predictions(p,minimum_dates=3,window_dates=10,coverage=.75)
        self.assertEqual(a["daily"][:13],b["daily"][:13])

    def test_degradation_is_review_not_automatic_retirement(self):
        p=streams()
        for row in p["ridge"][-30:]:
            row["prediction"]=-.5
        result=monitor_predictions(p,minimum_dates=3,window_dates=10,coverage=.75)
        self.assertEqual(result["model_review"]["ridge"]["status"],"REVIEW_FOR_DEGRADATION")
        self.assertFalse(result["model_review"]["ridge"]["automatic_retirement"])

    def test_duplicate_security_rejected(self):
        p=streams()
        for rows in p.values():
            rows.insert(1,dict(rows[0]))
        with self.assertRaisesRegex(ValueError,'MONITOR_DATE_OR_SECURITY_ORDER'):
            monitor_predictions(p)

    def test_partial_and_missing_dates_expire_calibration_and_champion(self):
        p=streams()
        for rows in p.values():
            for row in rows:
                if row["date"]>="2020-01-20" and row["security_id"]=="SYN:0":
                    row["actual"]=None
        report=monitor_predictions(p,minimum_dates=3,window_dates=5,coverage=.75)
        self.assertEqual(report["daily"][18]["champion"],"ridge")
        self.assertEqual(report["daily"][-1]["champion"],"historical_mean")
        self.assertEqual(report["daily"][-1]["matured_date_slots"],5)
        self.assertEqual(report["daily"][-1]["incomplete_matured_dates"],5)
        self.assertIsNone(report["daily"][-1]["models"]["ridge"]["interval_half_width"])
        relaxed=monitor_predictions(p,minimum_dates=3,window_dates=5,coverage=.75,
                                    minimum_label_fraction=.5)
        self.assertEqual(relaxed["daily"][-1]["champion"],"ridge")
        self.assertLess(relaxed["coverage_as_of_last_prediction"]["ridge"]["assessed_dates"],
                        len(relaxed["daily"])-3)
        self.assertFalse(relaxed["automatic_promotion_enabled"])

    def test_missing_and_alignment(self):
        p=streams()
        for rows in p.values():
            rows[0]["actual"]=None
        self.assertTrue(monitor_predictions(p,minimum_dates=3,window_dates=10)["daily"])
        p["ridge"].pop()
        with self.assertRaisesRegex(ValueError,"MONITOR_ALIGNMENT_MISMATCH"):
            monitor_predictions(p)
