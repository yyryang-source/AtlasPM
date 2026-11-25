import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch
from atlaspm.data.api_gateway import Gateway, GatewayError
from atlaspm.research import credit_intelligence as c


class CreditTests(unittest.TestCase):
    def test_documented_aggregate_adapter_and_no_pit_claim(self):
        body={"results":[{"identifierType":"LEI","identifierValue":"A"*20,
              "s&pRatingsData":[{"rxEntityId":1,"s&pData":[{"ratingType":"Long Term",
              "ratingValue":"BBB","ratingDate":"2020-01-01","ratingActionWord":"Downgrade"}]}],
              "moodysRatingsData":[{"moodysOrgId":"1","moodysData":[{"ratingTypeText":"Long Term",
              "ratingValue":"Ba1","ratingDate":"2020-01-01"}]}]}]}
        calls=[]
        def opener(req,timeout):
            calls.append(req)
            return BytesIO(json.dumps(body).encode())
        g=Gateway(opener=opener)
        with patch.dict(os.environ,{"XPRESSAPI_TOKEN":"fixture","ATLASPM_XPRESSAPI_RIGHTS":"fixture",
                                   "ATLASPM_XPRESSAPI_MAX_REQUESTS":"1"}):
            result=c.fetch_ratings("issuer","LEI",["A"*20],as_of="2020-01-02",gateway=g)
            self.assertIn("/agencyratings/api/v1/issuer?",calls[0].full_url)
            self.assertEqual(calls[0].get_header("Authorization"),"Bearer fixture")
            self.assertFalse(result["point_in_time_verified"])
            self.assertIsNone(result["ratings"][0]["published_at"])
            comparison=c.compare_reviewed(result["ratings"],common_basis_reviewed=True)
            self.assertTrue(comparison["investment_grade_split"])
            self.assertEqual(comparison["notch_range"],2)
            actions=c.review_actions(result)["actions"]
            self.assertEqual(actions[0]["action"],"downgrade")
            self.assertTrue(actions[0]["credit_review_requested"])
            self.assertIsNone(actions[0]["default_probability"])
            with self.assertRaisesRegex(GatewayError,"budget exhausted"):
                c.fetch_ratings("issuer","LEI",["A"*20],as_of="2020-01-02",gateway=g)

    def test_reject_bad_scope_and_unreviewed_comparison(self):
        with patch.dict(os.environ,{"ATLASPM_XPRESSAPI_RIGHTS":"fixture"}):
            with self.assertRaises(ValueError):
                c.fetch_ratings("issue","LEI",["A"*20],as_of="2020-01-02")
        with self.assertRaises(ValueError):
            c.compare_reviewed([],common_basis_reviewed=False)

    def test_bounded_historical_query_uses_documented_dates(self):
        calls=[]
        def opener(req,timeout):
            calls.append(req)
            return BytesIO(b'{"results":[]}')
        g=Gateway(opener=opener)
        with patch.dict(os.environ,{"XPRESSAPI_TOKEN":"fixture","ATLASPM_XPRESSAPI_RIGHTS":"fixture",
                                    "ATLASPM_XPRESSAPI_MAX_REQUESTS":"1"}):
            c.fetch_ratings("issue","CUSIP9",["123456789"],as_of="2020-03-01",
                            start_date="2020-01-01",gateway=g)
            self.assertIn("startDate=2020-01-01",calls[0].full_url)
            self.assertIn("endDate=2020-03-01",calls[0].full_url)
            self.assertNotIn("asOfDate",calls[0].full_url)
            with self.assertRaises(ValueError):
                c.fetch_ratings("issue","CUSIP9",["123456789"],as_of="2020-03-01",
                                start_date="2010-01-01",gateway=g)
