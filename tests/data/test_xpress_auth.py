import json
import os
import unittest
from io import BytesIO
from urllib.parse import parse_qs
from unittest.mock import patch
from atlaspm.data.api_gateway import Gateway
from atlaspm.research.credit_intelligence import XpressSession


class TokenTests(unittest.TestCase):
    def test_login_refresh_reuse_and_no_secret_telemetry(self):
        calls=[]
        def opener(req,timeout):
            calls.append(req)
            n=len(calls)
            return BytesIO(json.dumps({"token_type":"bearer","access_token":"secret-access-"+str(n),
                        "refresh_token":"secret-refresh-"+str(n),"expires_in_seconds":"60"}).encode())
        now=[0.0]
        g=Gateway(opener=opener,clock=lambda:now[0],sleep=lambda v:now.__setitem__(0,now[0]+v))
        session=XpressSession(gateway=g,clock=lambda:now[0])
        with patch.dict(os.environ,{"XPRESSAPI_USERNAME":"user","XPRESSAPI_PASSWORD":"secret-password",
                      "ATLASPM_XPRESSAPI_RIGHTS":"fixture","ATLASPM_XPRESSAPI_MAX_REQUESTS":"2"}):
            self.assertEqual(session.access(),"secret-access-1")
            self.assertEqual(session.access(),"secret-access-1")
            now[0]=31
            self.assertEqual(session.access(),"secret-access-2")
        self.assertTrue(calls[0].full_url.endswith("/authenticate/api/v1/token"))
        self.assertTrue(calls[1].full_url.endswith("/authenticate/api/v1/tokenRefresh"))
        self.assertEqual(parse_qs(calls[1].data.decode()),{"refreshToken":["secret-refresh-1"]})
        self.assertNotIn("secret",json.dumps(g.health()))
        self.assertNotIn("secret",repr(session))
        session.clear()


    def test_finra_oauth_fixed_destination_and_no_data_claim(self):
        calls=[]
        def opener(req,timeout):
            calls.append(req)
            return BytesIO(b'{"token_type":"Bearer","access_token":"fixture-access","expires_in":"3600"}')
        g=Gateway(opener=opener)
        with patch.dict(os.environ,{"FINRA_CLIENT_ID":"fixture-id","FINRA_CLIENT_SECRET":"fixture-secret",
                      "ATLASPM_FINRA_RIGHTS":"fixture","ATLASPM_FINRA_MAX_REQUESTS":"1"}):
            access,refresh,ttl=g.finra_token()
        self.assertEqual((access,refresh,ttl),("fixture-access",None,3600))
        self.assertTrue(calls[0].full_url.startswith("https://ews.fip.finra.org/"))
        self.assertTrue(calls[0].get_header("Authorization").startswith("Basic "))
        self.assertEqual(g.health()["finra"]["last_status"],"AUTH_SUCCESS")
        self.assertIsNone(g.health()["finra"]["last_success_utc"])
        self.assertNotIn("fixture-secret",json.dumps(g.health()))
